"""Mock payments (and the shared settlement logic used by the optional Razorpay integration).

Key guarantees
* The amount ALWAYS comes from the booking's grand_total - never from the client.
* Payment save + booking confirmation + seat reservation + ticket creation happen in ONE transaction.
* A booking row lock plus unique constraints make duplicate payments impossible.
* Late payments (seat hold expired) either confirm (seats still free) or refund automatically.
"""
import logging
import secrets
from decimal import Decimal
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BookedSeat, Booking, Coupon, Event, Payment, Refund, Show, User
from app.services import booking_service, effects, email_service, seat_service, ticket_service, waitlist_service
from app.utils.constants import (
    BookingStatus,
    PaymentMethod,
    PaymentStatus,
    RefundReason,
    RefundStatus,
    ShowStatus,
    SimulateStatus,
)
from app.utils.errors import Conflict, NotFound
from app.utils.time import utc_now

logger = logging.getLogger("app.payments")


def new_transaction_id() -> str:
    return "TXN-" + secrets.token_hex(8).upper()


def _lock_booking(db: Session, booking_id: int) -> Optional[Booking]:
    return db.execute(select(Booking).where(Booking.id == booking_id).with_for_update()).scalar_one_or_none()


def _seats_unavailable_for(db: Session, booking: Booking, seat_ids) -> bool:
    """True if someone else now holds / owns any of the booking's seats (ownership re-check)."""
    show = db.get(Show, booking.show_id)
    if show.status != ShowStatus.SCHEDULED.value:
        return True
    if seat_service.booked_seat_ids(db, booking.show_id) & set(seat_ids):
        return True
    r_locks = seat_service.get_redis().mget([seat_service.lock_key(booking.show_id, sid) for sid in seat_ids])
    for raw in r_locks:
        parsed = seat_service.decode_lock(raw)
        if parsed and parsed[0] != booking.user_id:
            return True
    return False


def _refund_late_payment(
    db: Session, booking: Booking, method: str, txn_id: str, gateway: str, gateway_payment_id: Optional[str]
) -> tuple:
    """Customer paid but the seats are gone: record the payment, refund 100%, cancel the booking."""
    now = utc_now()
    payment = Payment(
        booking_id=booking.id,
        transaction_id=txn_id,
        amount=booking.grand_total,
        payment_method=method,
        status=PaymentStatus.REFUNDED.value,
        paid_at=now,
        gateway=gateway,
        gateway_payment_id=gateway_payment_id,
        success_key=1,
    )
    db.add(payment)
    db.flush()
    refund = Refund(
        booking_id=booking.id,
        payment_id=payment.id,
        amount=booking.grand_total,
        reason=RefundReason.LATE_PAYMENT.value,
        status=RefundStatus.PROCESSED.value,
        refunded_at=now,
    )
    db.add(refund)
    booking.status = BookingStatus.CANCELLED.value
    booking.cancelled_at = now
    for bs in booking.seats:
        bs.active_key = None
    db.commit()
    return payment, refund


def settle_successful_payment(
    db: Session,
    booking: Booking,
    method: str,
    background: Optional[BackgroundTasks],
    gateway: str = "Mock",
    gateway_payment_id: Optional[str] = None,
) -> dict:
    """Core success path. ``booking`` must already be row-locked by the caller.

    Returns {"outcome": "confirmed"|"refunded", "payment": Payment, "booking": Booking, "refund": Refund|None}
    """
    txn_id = new_transaction_id()
    seat_ids = [bs.seat_id for bs in booking.seats]
    user = db.get(User, booking.user_id)
    booking_service.lock_show_row(db, booking.show_id)  # serialise with other bookings for this show

    if _seats_unavailable_for(db, booking, seat_ids):
        payment, refund = _refund_late_payment(db, booking, method, txn_id, gateway, gateway_payment_id)
        email_service.late_payment_refund(background, user, booking, booking.grand_total)
        effects._safe(effects.manager.publish_user_notification, user.id, "Seats were taken - full refund issued", "refund")
        return {"outcome": "refunded", "payment": payment, "booking": booking, "refund": refund}

    now = utc_now()
    try:
        payment = Payment(
            booking_id=booking.id,
            transaction_id=txn_id,
            amount=booking.grand_total,  # always server-side
            payment_method=method,
            status=PaymentStatus.SUCCESS.value,
            paid_at=now,
            gateway=gateway,
            gateway_payment_id=gateway_payment_id,
            success_key=1,
        )
        db.add(payment)
        booking.status = BookingStatus.CONFIRMED.value
        booking.confirmed_at = now
        for bs in booking.seats:
            bs.active_key = 1  # the unique (show, seat, active_key) index now reserves the seat permanently
        ticket_service.create_ticket_record(db, booking)
        if booking.coupon_code:
            db.execute(update(Coupon).where(Coupon.code == booking.coupon_code).values(used_count=Coupon.used_count + 1))
        db.commit()
    except IntegrityError:
        # A concurrent confirmation won the race for a seat (or a duplicate payment slipped in).
        db.rollback()
        booking = _lock_booking(db, booking.id)
        if booking.status == BookingStatus.CONFIRMED.value:
            raise Conflict("This booking has already been paid")
        payment, refund = _refund_late_payment(db, booking, method, txn_id, gateway, gateway_payment_id)
        email_service.late_payment_refund(background, user, booking, booking.grand_total)
        return {"outcome": "refunded", "payment": payment, "booking": booking, "refund": refund}

    effects.after_booking_confirmed(db, booking, background)
    return {"outcome": "confirmed", "payment": payment, "booking": booking, "refund": None}


def pay_booking(
    db: Session,
    user: User,
    booking_id: int,
    method: PaymentMethod,
    simulate: SimulateStatus,
    background: BackgroundTasks,
) -> dict:
    booking = _lock_booking(db, booking_id)
    if booking is None or booking.user_id != user.id:
        raise NotFound("Booking not found")
    if booking.status == BookingStatus.CONFIRMED.value:
        raise Conflict("This booking has already been paid and confirmed")
    if booking.status == BookingStatus.CANCELLED.value:
        raise Conflict("This booking was cancelled and cannot be paid")
    if booking.status == BookingStatus.EXPIRED.value:
        raise Conflict("This booking has expired; please select seats again")

    if simulate == SimulateStatus.FAILED:
        payment = Payment(
            booking_id=booking.id,
            transaction_id=new_transaction_id(),
            amount=booking.grand_total,
            payment_method=method.value,
            status=PaymentStatus.FAILED.value,
            paid_at=utc_now(),
            gateway="Mock",
            success_key=None,
        )
        db.add(payment)
        db.commit()
        email_service.payment_failed(background, user, booking)
        return {"outcome": "failed", "payment": payment, "booking": booking, "refund": None}

    # Success path (works even when the seat hold has just expired but the expiry job has not run yet:
    # settle_successful_payment re-checks ownership and refunds automatically if the seats were taken).
    return settle_successful_payment(db, booking, method.value, background)


def list_payments(db: Session, user: User, booking_id: int):
    booking = booking_service.get_booking(db, user, booking_id)
    return list(db.scalars(select(Payment).where(Payment.booking_id == booking.id).order_by(Payment.id)))
