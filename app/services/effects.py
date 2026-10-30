"""Side effects that must run AFTER a database transaction has committed successfully.

Order matters: database first, then Redis, then WebSocket broadcasts and emails. Everything in
here is best-effort and must never raise into the caller (the money/seat state is already final).
"""
import logging
from typing import Iterable, List, Optional

from fastapi import BackgroundTasks
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import BookedSeat, Booking, Event, Show, User, Waitlist
from app.services import email_service, seat_service, ticket_service
from app.utils.constants import SeatState
from app.websockets.manager import manager

logger = logging.getLogger("app.effects")


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("post-commit side effect failed: %s", getattr(fn, "__name__", fn))
        return None


def booking_seat_ids(db: Session, booking_id: int) -> List[int]:
    return list(db.scalars(select(BookedSeat.seat_id).where(BookedSeat.booking_id == booking_id)))


def release_booking_locks(booking: Booking, seat_ids: Iterable[int]) -> None:
    keys = [seat_service.lock_key(booking.show_id, sid) for sid in seat_ids]
    seat_service.release_locks_owned_by(booking.user_id, keys)


def seats_freed(booking: Booking, seat_ids: List[int]) -> None:
    """Redis locks released and seats open again: tell connected clients."""
    manager.publish_seats(booking.show_id, "released", seat_ids, SeatState.AVAILABLE.value)


def after_booking_confirmed(db: Session, booking: Booking, background: Optional[BackgroundTasks]) -> None:
    seat_ids = booking_seat_ids(db, booking.id)
    _safe(release_booking_locks, booking, seat_ids)
    _safe(manager.publish_seats, booking.show_id, "booked", seat_ids, SeatState.BOOKED.value)
    # The customer no longer needs to wait for these seats.
    try:
        db.execute(delete(Waitlist).where(Waitlist.show_id == booking.show_id, Waitlist.user_id == booking.user_id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("could not clear waitlist entry")
    pdf_path = _safe(ticket_service.generate_pdf, db, booking)
    show = db.get(Show, booking.show_id)
    event = db.get(Event, show.event_id)
    user = db.get(User, booking.user_id)
    _safe(email_service.booking_confirmed, background, user, booking, event.title, pdf_path)
    _safe(manager.publish_user_notification, user.id, f"Booking {booking.booking_number} confirmed", "success")
