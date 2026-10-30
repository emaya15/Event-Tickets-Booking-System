"""Bookings: bill calculation, creation (from locked seats), listing and visibility rules."""
import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BookedSeat, Booking, Event, Screen, Seat, Show, User, Venue
from app.schemas.booking import BookedSeatOut, BookingCreate, BookingOut
from app.services import coupon_service, seat_service
from app.utils.constants import BookingStatus, Role
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound
from app.utils.pagination import Pagination
from app.utils.time import utc_now

logger = logging.getLogger("app.bookings")
TWO = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def calculate_bill(prices: List[Decimal], discount: Decimal = Decimal("0")) -> dict:
    """Ticket total, convenience fee (Rs.30/ticket), GST (18% of tickets + fee) and grand total.

    A coupon discount (bonus feature) reduces the ticket total before GST is applied.
    """
    ticket_total = money(sum((Decimal(p) for p in prices), Decimal("0")))
    discount = money(discount)
    fee = money(Decimal(settings.CONVENIENCE_FEE_PER_TICKET) * len(prices))
    taxable = ticket_total - discount + fee
    gst = money(taxable * Decimal(settings.GST_RATE))
    return {
        "ticket_total": ticket_total,
        "discount_amount": discount,
        "convenience_fee": fee,
        "gst_amount": gst,
        "grand_total": money(taxable + gst),
    }


def next_booking_number(db: Session) -> str:
    """BKG-YYYYMMDD-NNNN, numbered per day. The unique index guards against races (caller retries)."""
    prefix = f"BKG-{utc_now():%Y%m%d}-"
    last = db.scalar(
        select(Booking.booking_number)
        .where(Booking.booking_number.like(prefix + "%"))
        .order_by(Booking.booking_number.desc())
        .limit(1)
    )
    sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{sequence:04d}"


def lock_show_row(db: Session, show_id: int) -> Show:
    """SELECT ... FOR UPDATE on the show row so bookings/payments for one show are serialised."""
    show = db.execute(select(Show).where(Show.id == show_id).with_for_update()).scalar_one_or_none()
    if show is None:
        raise NotFound("Show not found")
    return show


def create_booking(db: Session, user: User, data: BookingCreate) -> Booking:
    show = db.get(Show, data.show_id)
    if show is None:
        raise NotFound("Show not found")
    seat_service.assert_show_bookable(show)

    seat_ids = list(data.seat_ids)
    seats = {s.id: s for s in db.scalars(select(Seat).where(Seat.id.in_(seat_ids)))}
    if len(seats) != len(seat_ids) or any(s.screen_id != show.screen_id for s in seats.values()):
        raise BadRequest("One or more seats do not belong to this show's screen")
    if any(not s.is_active for s in seats.values()):
        raise BadRequest("One or more seats are unavailable")

    # Ownership check #1: every seat must still be locked by this customer.
    locks = seat_service.verify_locks_owned(show.id, user.id, seat_ids)

    # Serialise with other bookings/payments for this show, then re-check the database.
    lock_show_row(db, show.id)
    already_booked = seat_service.booked_seat_ids(db, show.id) & set(seat_ids)
    if already_booked:
        raise Conflict("Some seats were just booked by someone else", extra={"booked_seat_ids": sorted(already_booked)})
    now = utc_now()
    pending_clash = db.scalar(
        select(func.count())
        .select_from(BookedSeat)
        .join(Booking, Booking.id == BookedSeat.booking_id)
        .where(
            BookedSeat.show_id == show.id,
            BookedSeat.seat_id.in_(seat_ids),
            Booking.status == BookingStatus.PENDING.value,
            Booking.expires_at > now,
        )
    )
    if pending_clash:
        raise Conflict("You already have a booking awaiting payment for some of these seats")

    prices = [locks[sid][0] for sid in seat_ids]  # price captured when the seat was locked
    discount = Decimal("0")
    coupon = None
    if data.coupon_code:
        coupon, discount = coupon_service.validate_and_price(
            db, data.coupon_code, len(seat_ids), money(sum(prices, Decimal("0")))
        )
    bill = calculate_bill(prices, discount)

    # The booking may not outlive the shortest seat lock.
    shortest_ttl = min(ttl for _, ttl in locks.values())
    expires_at = now + timedelta(seconds=shortest_ttl)

    for attempt in range(5):
        booking = Booking(
            booking_number=next_booking_number(db),
            user_id=user.id,
            show_id=show.id,
            seat_count=len(seat_ids),
            coupon_code=coupon.code if coupon else None,
            status=BookingStatus.PENDING.value,
            expires_at=expires_at,
            **bill,
        )
        booking.seats = [
            BookedSeat(show_id=show.id, seat_id=sid, price=locks[sid][0], active_key=None) for sid in seat_ids
        ]
        db.add(booking)
        try:
            db.commit()
            return booking
        except IntegrityError:
            db.rollback()
            lock_show_row(db, show.id)
            if attempt == 4:
                raise Conflict("Could not allocate a booking number, please retry")
    raise Conflict("Could not create the booking, please retry")  # pragma: no cover


# ------------------------------------------------------------- presentation
def booking_to_out(db: Session, booking: Booking) -> BookingOut:
    extra = {"seats", "event_title", "show_start_at", "venue_name", "screen_name", "customer_name"}
    out = BookingOut(**{name: getattr(booking, name) for name in BookingOut.model_fields if name not in extra})
    rows = db.execute(
        select(BookedSeat.seat_id, Seat.row_label, Seat.seat_number, Seat.category, BookedSeat.price)
        .join(Seat, Seat.id == BookedSeat.seat_id)
        .where(BookedSeat.booking_id == booking.id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()
    out.seats = [
        BookedSeatOut(seat_id=r[0], row_label=r[1], seat_number=r[2], category=r[3], price=r[4]) for r in rows
    ]
    show = db.get(Show, booking.show_id)
    event = db.get(Event, show.event_id)
    screen = db.get(Screen, show.screen_id)
    venue = db.get(Venue, screen.venue_id)
    customer = db.get(User, booking.user_id)
    out.event_title, out.show_start_at = event.title, show.start_at
    out.venue_name, out.screen_name, out.customer_name = venue.name, screen.name, customer.name
    return out


def can_view_booking(db: Session, user: User, booking: Booking) -> bool:
    if user.role == Role.ADMIN.value:
        return True
    if user.role == Role.CUSTOMER.value:
        return booking.user_id == user.id
    if user.role == Role.ORGANIZER.value:
        show = db.get(Show, booking.show_id)
        event = db.get(Event, show.event_id)
        return event.organizer_id == user.id
    return False


def get_booking(db: Session, user: User, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    # 404 (not 403) so that booking ids of other customers are not discoverable.
    if booking is None or not can_view_booking(db, user, booking):
        raise NotFound("Booking not found")
    return booking


def list_bookings(
    db: Session,
    user: User,
    p: Pagination,
    status: Optional[str] = None,
    show_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
):
    if user.role == Role.STAFF.value:
        raise Forbidden("Staff cannot list bookings")
    valid_statuses = {s.value for s in BookingStatus}
    if status and status not in valid_statuses:
        raise BadRequest(f"status must be one of: {', '.join(sorted(valid_statuses))}")
    if from_date and to_date and from_date > to_date:
        raise BadRequest("from_date must not be after to_date")

    stmt = select(Booking)
    if user.role == Role.CUSTOMER.value:
        stmt = stmt.where(Booking.user_id == user.id)
    elif user.role == Role.ORGANIZER.value:
        stmt = (
            stmt.join(Show, Show.id == Booking.show_id)
            .join(Event, Event.id == Show.event_id)
            .where(Event.organizer_id == user.id)
        )
    if status:
        stmt = stmt.where(Booking.status == status)
    if show_id:
        stmt = stmt.where(Booking.show_id == show_id)
    if from_date:
        stmt = stmt.where(Booking.created_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        stmt = stmt.where(Booking.created_at < datetime.combine(to_date + timedelta(days=1), datetime.min.time()))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.order_by(Booking.id.desc()).offset(p.skip).limit(p.limit)).all()
    return [booking_to_out(db, b) for b in rows], total
