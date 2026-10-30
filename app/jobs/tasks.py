"""Scheduled job bodies. Each opens its own DB session and never raises."""
import logging
from datetime import timedelta

from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import BookedSeat, Booking, Event, Show, User, Waitlist
from app.services import cache_service, effects, email_service, waitlist_service
from app.utils.constants import BookingStatus, EventStatus, ShowStatus, WaitlistStatus
from app.utils.time import utc_now

logger = logging.getLogger("app.jobs")


def expire_pending_bookings() -> int:
    """Every minute: Pending Payment bookings past expires_at become Expired and free their seats."""
    db = SessionLocal()
    try:
        now = utc_now()
        ids = list(
            db.scalars(
                select(Booking.id)
                .where(Booking.status == BookingStatus.PENDING.value, Booking.expires_at <= now)
                .limit(500)
            )
        )
        shows = set()
        for booking_id in ids:
            # Conditional update: a booking paid a split second ago is not touched.
            res = db.execute(
                update(Booking)
                .where(Booking.id == booking_id, Booking.status == BookingStatus.PENDING.value)
                .values(status=BookingStatus.EXPIRED.value, updated_at=now)
            )
            db.commit()
            if res.rowcount:
                booking = db.get(Booking, booking_id)
                seat_ids = effects.booking_seat_ids(db, booking_id)
                effects._safe(effects.release_booking_locks, booking, seat_ids)
                effects._safe(effects.seats_freed, booking, seat_ids)
                shows.add(booking.show_id)
        # Locks that simply timed out in Redis may also have opened seats for waiting customers.
        waiting_shows = set(db.scalars(select(Waitlist.show_id).where(Waitlist.status == WaitlistStatus.WAITING.value).distinct()))
        for show_id in shows | waiting_shows:
            effects._safe(waitlist_service.process_waitlist, db, show_id, None)
        if ids:
            logger.info("expired %d pending bookings", len(ids))
        return len(ids)
    except Exception:
        logger.exception("expire_pending_bookings failed")
        db.rollback()
        return 0
    finally:
        db.close()


def send_show_reminders() -> int:
    """Every 30 min: remind customers whose show starts within 3 hours (once per booking)."""
    db = SessionLocal()
    sent = 0
    try:
        now = utc_now()
        rows = db.execute(
            select(Booking, Show, Event)
            .join(Show, Show.id == Booking.show_id)
            .join(Event, Event.id == Show.event_id)
            .where(
                Booking.status == BookingStatus.CONFIRMED.value,
                Booking.reminder_sent_at.is_(None),
                Show.status == ShowStatus.SCHEDULED.value,
                Show.start_at > now,
                Show.start_at <= now + timedelta(hours=3),
            )
        ).all()
        for booking, show, event in rows:
            # Claim first (conditional update) so overlapping runs never double-send.
            claimed = db.execute(
                update(Booking).where(Booking.id == booking.id, Booking.reminder_sent_at.is_(None)).values(reminder_sent_at=now)
            )
            db.commit()
            if claimed.rowcount:
                user = db.get(User, booking.user_id)
                email_service.show_reminder(None, user, booking, event.title, show.start_at)
                sent += 1
        return sent
    except Exception:
        logger.exception("send_show_reminders failed")
        db.rollback()
        return sent
    finally:
        db.close()


def complete_shows() -> int:
    """Every 15 min: Scheduled shows whose end time has passed become Completed."""
    db = SessionLocal()
    try:
        now = utc_now()
        res = db.execute(
            update(Show)
            .where(Show.status == ShowStatus.SCHEDULED.value, Show.end_at <= now)
            .values(status=ShowStatus.COMPLETED.value, updated_at=now)
        )
        db.commit()
        if res.rowcount:
            cache_service.invalidate_event_cache()
        return res.rowcount
    except Exception:
        logger.exception("complete_shows failed")
        db.rollback()
        return 0
    finally:
        db.close()


def complete_events() -> int:
    """Daily: an event is Completed when it has shows and every non-cancelled show is Completed."""
    db = SessionLocal()
    try:
        done = 0
        events = db.scalars(select(Event).where(Event.status == EventStatus.PUBLISHED.value)).all()
        for event in events:
            statuses = set(db.scalars(select(Show.status).where(Show.event_id == event.id)))
            live = statuses - {ShowStatus.CANCELLED.value}
            if live and live == {ShowStatus.COMPLETED.value}:
                event.status = EventStatus.COMPLETED.value
                done += 1
        db.commit()
        if done:
            cache_service.invalidate_event_cache()
        return done
    except Exception:
        logger.exception("complete_events failed")
        db.rollback()
        return 0
    finally:
        db.close()
