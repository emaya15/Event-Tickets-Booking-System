"""Customer cancellations, organizer show cancellations and refunds (all mock refunds)."""
import logging
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BookedSeat, Booking, Event, Payment, Refund, Show, User
from app.services import booking_service, cache_service, effects, email_service, event_service, seat_service, waitlist_service
from app.utils.constants import (
    BookingStatus,
    PaymentStatus,
    RefundReason,
    RefundStatus,
    Role,
    SeatState,
    ShowStatus,
)
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound
from app.utils.time import utc_now
from app.models import Ticket

logger = logging.getLogger("app.cancel")
money = booking_service.money


def customer_refund_amount(booking: Booking, show: Show, now) -> Decimal:
    """Policy: >24h before -> 100% of ticket total, 2-24h -> 50%, <2h -> not allowed.

    Convenience fee and its GST are never refunded on customer cancellations. The "ticket total" is
    what the customer actually paid for tickets (after any coupon discount).
    """
    time_left = show.start_at - now
    if time_left < timedelta(hours=2):
        raise BadRequest("Cancellation is not allowed less than 2 hours before the show")
    ticket_paid = Decimal(booking.ticket_total) - Decimal(booking.discount_amount)
    if time_left > timedelta(hours=24):
        return money(ticket_paid)
    return money(ticket_paid * Decimal("0.5"))


def _successful_payment(db: Session, booking_id: int) -> Optional[Payment]:
    return db.scalar(
        select(Payment)
        .where(Payment.booking_id == booking_id, Payment.status == PaymentStatus.SUCCESS.value)
        .order_by(Payment.id.desc())
    )


def cancel_booking(db: Session, user: User, booking_id: int, background: BackgroundTasks) -> dict:
    booking = db.execute(select(Booking).where(Booking.id == booking_id).with_for_update()).scalar_one_or_none()
    if booking is None or (user.role != Role.ADMIN.value and booking.user_id != user.id):
        raise NotFound("Booking not found")
    if booking.status not in (BookingStatus.CONFIRMED.value, BookingStatus.PENDING.value):
        raise Conflict(f"A {booking.status} booking cannot be cancelled")
    show = db.get(Show, booking.show_id)
    now = utc_now()
    seat_ids = effects.booking_seat_ids(db, booking.id)
    customer = db.get(User, booking.user_id)

    refund = None
    refund_amount = Decimal("0.00")
    if booking.status == BookingStatus.CONFIRMED.value:
        ticket = db.scalar(select(Ticket).where(Ticket.booking_id == booking.id))
        if ticket is not None and ticket.is_used:
            raise Conflict("This ticket has already been used and cannot be cancelled")
        refund_amount = customer_refund_amount(booking, show, now)
        payment = _successful_payment(db, booking.id)
        refund = Refund(
            booking_id=booking.id,
            payment_id=payment.id if payment else None,
            amount=refund_amount,
            reason=RefundReason.CUSTOMER.value,
            status=RefundStatus.PROCESSED.value,
            refunded_at=now,
        )
        db.add(refund)
        if payment:
            payment.status = PaymentStatus.REFUNDED.value
        for bs in booking.seats:
            bs.active_key = None  # seat is free for others again
    booking.status = BookingStatus.CANCELLED.value
    booking.cancelled_at = now
    db.commit()

    # ---- after commit: Redis -> WebSocket -> waitlist -> email
    effects._safe(effects.release_booking_locks, booking, seat_ids)
    effects._safe(effects.seats_freed, booking, seat_ids)
    effects._safe(waitlist_service.process_waitlist, db, booking.show_id, background)
    effects._safe(email_service.booking_cancelled, background, customer, booking, refund_amount)
    return {"booking": booking, "refund": refund, "refund_amount": refund_amount}


def cancel_show(db: Session, user: User, show_id: int, background: BackgroundTasks) -> Show:
    show = event_service.get_show_or_404(db, show_id)
    event = db.get(Event, show.event_id)
    event_service.assert_can_manage_event(user, event)
    if show.status == ShowStatus.CANCELLED.value:
        raise Conflict("This show is already cancelled")
    if show.status == ShowStatus.COMPLETED.value:
        raise BadRequest("A completed show cannot be cancelled")

    booking_service.lock_show_row(db, show.id)
    now = utc_now()
    bookings = db.scalars(
        select(Booking)
        .where(Booking.show_id == show.id, Booking.status.in_([BookingStatus.CONFIRMED.value, BookingStatus.PENDING.value]))
        .with_for_update()
    ).all()

    notices: List[tuple] = []  # (customer, booking, refund_amount) emailed after commit
    all_seat_ids: List[int] = []
    for booking in bookings:
        seat_ids = effects.booking_seat_ids(db, booking.id)
        all_seat_ids.extend(seat_ids)
        if booking.status == BookingStatus.CONFIRMED.value:
            payment = _successful_payment(db, booking.id)
            db.add(
                Refund(
                    booking_id=booking.id,
                    payment_id=payment.id if payment else None,
                    amount=booking.grand_total,  # 100% including convenience fee and GST
                    reason=RefundReason.SHOW.value,
                    status=RefundStatus.PROCESSED.value,
                    refunded_at=now,
                )
            )
            if payment:
                payment.status = PaymentStatus.REFUNDED.value
            notices.append((db.get(User, booking.user_id), booking, Decimal(booking.grand_total)))
        for bs in booking.seats:
            bs.active_key = None
        booking.status = BookingStatus.CANCELLED.value
        booking.cancelled_at = now
    show.status = ShowStatus.CANCELLED.value
    db.commit()

    # ---- after commit
    effects._safe(_clear_show_locks, show)
    effects._safe(effects.manager.publish_seats, show.id, "cancelled", sorted(set(all_seat_ids)), SeatState.AVAILABLE.value)
    effects._safe(effects.manager.publish_show_cancelled, show.id, f"The show for {event.title} was cancelled")
    for customer, booking, amount in notices:
        effects._safe(email_service.show_cancelled, background, customer, booking, event.title, amount)
        effects._safe(
            effects.manager.publish_user_notification,
            customer.id,
            f"{event.title} was cancelled. Full refund of Rs. {float(amount):,.2f} issued.",
            "refund",
        )
    cache_service.invalidate_event_cache()
    return show


def _clear_show_locks(show: Show) -> None:
    r = seat_service.get_redis()
    keys = list(r.scan_iter(match=f"lock:show:{show.id}:seat:*", count=500))
    if keys:
        r.delete(*keys)


def list_refunds(db: Session, user: User, booking_id: int):
    booking = booking_service.get_booking(db, user, booking_id)
    return list(db.scalars(select(Refund).where(Refund.booking_id == booking.id).order_by(Refund.id)))
