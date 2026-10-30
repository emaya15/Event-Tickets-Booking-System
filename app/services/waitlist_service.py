"""Waitlist for sold-out shows, notified in queue order when suitable seats free up."""
import logging
from collections import Counter
from typing import List, Optional

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, Event, Show, User, Waitlist
from app.schemas.booking import WaitlistCreate, WaitlistOut
from app.services import email_service, seat_service
from app.utils.constants import BookingStatus, Role, SeatState, ShowStatus, WaitlistStatus
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound
from app.utils.pagination import Pagination
from app.utils.time import utc_now
from app.websockets.manager import manager

logger = logging.getLogger("app.waitlist")


def is_sold_out(db: Session, show: Show) -> bool:
    """Sold out = no seat is currently Available (every active seat is Booked or held by a lock)."""
    states = seat_service.compute_seat_states(db, show)
    return not any(s["status"] == SeatState.AVAILABLE.value for s in states)


def join(db: Session, user: User, show_id: int, data: WaitlistCreate) -> Waitlist:
    show = db.get(Show, show_id)
    if show is None:
        raise NotFound("Show not found")
    seat_service.assert_show_bookable(show)
    if not is_sold_out(db, show):
        raise BadRequest("The waitlist is only for sold-out shows; seats are still available")
    if db.scalar(select(Waitlist.id).where(Waitlist.show_id == show_id, Waitlist.user_id == user.id)):
        raise Conflict("You are already on the waitlist for this show")
    has_booking = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.show_id == show_id,
            Booking.user_id == user.id,
            Booking.status.in_([BookingStatus.CONFIRMED.value, BookingStatus.PENDING.value]),
        )
    )
    if has_booking:
        raise Conflict("You already have a booking for this show")
    entry = Waitlist(
        show_id=show_id,
        user_id=user.id,
        seats_requested=data.seats_requested,
        preferred_category=data.preferred_category,
        status=WaitlistStatus.WAITING.value,
    )
    db.add(entry)
    db.commit()
    return entry


def _position(db: Session, entry: Waitlist) -> int:
    ahead = db.scalar(
        select(func.count())
        .select_from(Waitlist)
        .where(Waitlist.show_id == entry.show_id, Waitlist.id <= entry.id, Waitlist.status == WaitlistStatus.WAITING.value)
    )
    return int(ahead or 0)


def to_out(db: Session, entry: Waitlist) -> WaitlistOut:
    out = WaitlistOut.model_validate(entry)
    out.position = _position(db, entry) if entry.status == WaitlistStatus.WAITING.value else None
    return out


def list_entries(db: Session, user: User, show_id: int, p: Pagination):
    show = db.get(Show, show_id)
    if show is None:
        raise NotFound("Show not found")
    stmt = select(Waitlist).where(Waitlist.show_id == show_id)
    if user.role == Role.CUSTOMER.value:
        stmt = stmt.where(Waitlist.user_id == user.id)
    elif user.role == Role.ORGANIZER.value:
        event = db.get(Event, show.event_id)
        if event.organizer_id != user.id:
            raise Forbidden("You can only view the waitlist of your own events")
    elif user.role != Role.ADMIN.value:
        raise Forbidden("You do not have permission to view the waitlist")
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.order_by(Waitlist.id).offset(p.skip).limit(p.limit)).all()
    return [to_out(db, r) for r in rows], total


def remove(db: Session, user: User, waitlist_id: int) -> None:
    entry = db.get(Waitlist, waitlist_id)
    if entry is None:
        raise NotFound("Waitlist entry not found")
    if user.role != Role.ADMIN.value and entry.user_id != user.id:
        raise NotFound("Waitlist entry not found")
    db.delete(entry)
    db.commit()


def process_waitlist(db: Session, show_id: int, background: Optional[BackgroundTasks] = None) -> int:
    """Notify waiting customers, in queue order, whose request now fits. Returns how many were notified."""
    show = db.get(Show, show_id)
    if show is None or show.status != ShowStatus.SCHEDULED.value or utc_now() >= show.booking_closes_at:
        return 0
    entries = db.scalars(
        select(Waitlist)
        .where(Waitlist.show_id == show_id, Waitlist.status == WaitlistStatus.WAITING.value)
        .order_by(Waitlist.id)
    ).all()
    if not entries:
        return 0
    states = seat_service.compute_seat_states(db, show)
    available: List[str] = [s["category"] for s in states if s["status"] == SeatState.AVAILABLE.value]
    if not available:
        return 0
    event = db.get(Event, show.event_id)
    to_notify: List[User] = []
    for entry in entries:  # strictly in queue order; each notified entry "reserves" its share of the pool
        pool = Counter(available)
        if entry.preferred_category:
            if pool[entry.preferred_category] < entry.seats_requested:
                continue
            taken = [entry.preferred_category] * entry.seats_requested
        else:
            if len(available) < entry.seats_requested:
                continue
            taken = available[: entry.seats_requested]
        for cat in taken:
            available.remove(cat)
        entry.status = WaitlistStatus.NOTIFIED.value
        entry.notified_at = utc_now()
        to_notify.append(db.get(User, entry.user_id))
    db.commit()
    # Notifications go out only after the status change is safely committed.
    for user in to_notify:
        email_service.waitlist_available(background, user, event.title, show_id)
        manager.publish_user_notification(user.id, f"Seats are available for {event.title}", "waitlist")
    return len(to_notify)
