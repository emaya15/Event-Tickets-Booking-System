"""Venue, screen and seat management (Admin only)."""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BookedSeat, Booking, Screen, Seat, Show, Venue
from app.schemas.venue import BulkSeatCreate, ScreenCreate, VenueCreate, VenueUpdate
from app.utils.constants import BookingStatus, ShowStatus
from app.utils.errors import BadRequest, Conflict, NotFound
from app.utils.pagination import Pagination
from app.utils.time import utc_now


def create_venue(db: Session, data: VenueCreate) -> Venue:
    venue = Venue(**data.model_dump())
    db.add(venue)
    db.commit()
    return venue


def list_venues(db: Session, p: Pagination, city: Optional[str], include_inactive: bool):
    stmt = select(Venue)
    if not include_inactive:
        stmt = stmt.where(Venue.is_active.is_(True))
    if city:
        stmt = stmt.where(func.lower(Venue.city) == city.strip().lower())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Venue.id).offset(p.skip).limit(p.limit)).all()
    return items, total


def get_venue(db: Session, venue_id: int) -> Venue:
    venue = db.get(Venue, venue_id)
    if venue is None:
        raise NotFound("Venue not found")
    return venue


def update_venue(db: Session, venue_id: int, data: VenueUpdate) -> Venue:
    venue = get_venue(db, venue_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(venue, field, value)
    db.commit()
    return venue


def create_screen(db: Session, venue_id: int, data: ScreenCreate) -> Screen:
    venue = get_venue(db, venue_id)
    if not venue.is_active:
        raise BadRequest("Cannot add screens to an inactive venue")
    exists = db.scalar(select(Screen.id).where(Screen.venue_id == venue_id, Screen.name == data.name))
    if exists:
        raise Conflict("A screen with this name already exists in the venue")
    screen = Screen(venue_id=venue_id, name=data.name, total_seats=data.total_seats)
    db.add(screen)
    db.commit()
    return screen


def list_screens(db: Session, venue_id: int):
    get_venue(db, venue_id)
    return db.scalars(select(Screen).where(Screen.venue_id == venue_id).order_by(Screen.id)).all()


def get_screen(db: Session, screen_id: int) -> Screen:
    screen = db.get(Screen, screen_id)
    if screen is None:
        raise NotFound("Screen not found")
    return screen


def bulk_create_seats(db: Session, screen_id: int, data: BulkSeatCreate) -> dict:
    screen = get_screen(db, screen_id)
    existing_count = db.scalar(select(func.count()).select_from(Seat).where(Seat.screen_id == screen_id))
    new_count = len(data.row_labels) * data.seats_per_row
    if existing_count + new_count > screen.total_seats:
        raise BadRequest(
            f"Screen capacity is {screen.total_seats} seats; it already has {existing_count} "
            f"and this request adds {new_count}"
        )
    wanted = {
        (row, number)
        for row in data.row_labels
        for number in range(data.start_number, data.start_number + data.seats_per_row)
    }
    clashes = db.execute(
        select(Seat.row_label, Seat.seat_number).where(Seat.screen_id == screen_id, Seat.row_label.in_(data.row_labels))
    ).all()
    duplicates = sorted(f"{r}{n}" for r, n in clashes if (r, n) in wanted)
    if duplicates:
        raise Conflict("Some seats already exist on this screen", extra={"duplicate_seats": duplicates[:50]})
    db.add_all(
        Seat(screen_id=screen_id, row_label=row, seat_number=number, category=data.category.value, is_active=True)
        for row, number in sorted(wanted)
    )
    db.commit()
    return {"screen_id": screen_id, "created": new_count, "total_seats_now": existing_count + new_count}


def _row_sort_key(label: str):
    return (len(label), label)


def get_layout(db: Session, screen_id: int) -> dict:
    """Seat layout grouped by row, ordered by seat number. Inactive seats show as Unavailable."""
    screen = get_screen(db, screen_id)
    seats = db.scalars(
        select(Seat).where(Seat.screen_id == screen_id).order_by(Seat.row_label, Seat.seat_number)
    ).all()
    rows: dict = {}
    for seat in seats:
        rows.setdefault(seat.row_label, []).append(
            {
                "id": seat.id,
                "seat_number": seat.seat_number,
                "category": seat.category,
                "is_active": seat.is_active,
                "status": "Available" if seat.is_active else "Unavailable",
            }
        )
    return {
        "screen_id": screen.id,
        "name": screen.name,
        "venue_id": screen.venue_id,
        "total_seats": screen.total_seats,
        "seat_count": len(seats),
        "rows": [{"row_label": label, "seats": rows[label]} for label in sorted(rows, key=_row_sort_key)],
    }


def set_seat_active(db: Session, seat_id: int, is_active: bool) -> Seat:
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise NotFound("Seat not found")
    if not is_active:
        # Do not disable a seat that is part of a live booking for an upcoming show.
        in_use = db.scalar(
            select(func.count())
            .select_from(BookedSeat)
            .join(Booking, Booking.id == BookedSeat.booking_id)
            .join(Show, Show.id == BookedSeat.show_id)
            .where(
                BookedSeat.seat_id == seat_id,
                Show.start_at > utc_now(),
                Show.status == ShowStatus.SCHEDULED.value,
                Booking.status.in_([BookingStatus.CONFIRMED.value, BookingStatus.PENDING.value]),
            )
        )
        if in_use:
            raise Conflict("Seat is part of an active booking for an upcoming show")
    seat.is_active = is_active
    db.commit()
    return seat
