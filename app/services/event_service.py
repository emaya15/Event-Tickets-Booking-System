"""Events and shows: creation rules, overlap checks, filtering, caching."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, Event, Screen, Seat, Show, ShowPrice, User, Venue
from app.schemas.event import EventCreate, EventListItem, EventOut, EventUpdate, ShowCreate, ShowOut, ShowUpdate
from app.services import cache_service
from app.utils.constants import BookingStatus, EventStatus, Role, SeatCategory, ShowStatus
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound
from app.utils.pagination import Pagination
from app.utils.time import utc_now


# ------------------------------------------------------------------ events
def create_event(db: Session, organizer: User, data: EventCreate) -> Event:
    if organizer.role == Role.ORGANIZER.value and not organizer.is_approved:
        raise Forbidden("Your organizer account has not been approved by an Admin yet")
    event = Event(
        organizer_id=organizer.id,
        title=data.title.strip(),
        category=data.category.value,
        language=data.language.strip(),
        description=data.description,
        duration_minutes=data.duration_minutes,
        age_rating=data.age_rating,
        status=data.status.value,
    )
    db.add(event)
    db.commit()
    cache_service.invalidate_event_cache()
    return event


def get_event_or_404(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise NotFound("Event not found")
    return event


def assert_can_manage_event(user: User, event: Event) -> None:
    """Organizers may manage only their own events; Admins may manage any."""
    if user.role == Role.ADMIN.value:
        return
    if user.role == Role.ORGANIZER.value and event.organizer_id == user.id:
        return
    raise Forbidden("You can only manage your own events")


def can_view_event(user: Optional[User], event: Event) -> bool:
    if event.status == EventStatus.PUBLISHED.value:
        return True
    if user is None:
        return False
    return user.role == Role.ADMIN.value or (user.role == Role.ORGANIZER.value and event.organizer_id == user.id)


def update_event(db: Session, user: User, event_id: int, data: EventUpdate) -> Event:
    event = get_event_or_404(db, event_id)
    assert_can_manage_event(user, event)
    changes = data.model_dump(exclude_unset=True)
    if event.status in (EventStatus.CANCELLED.value, EventStatus.COMPLETED.value):
        raise BadRequest(f"A {event.status} event cannot be modified")
    new_status = changes.get("status")
    if new_status is not None:
        if new_status == EventStatus.COMPLETED:
            raise BadRequest("Events are marked Completed automatically after their last show")
        if new_status == EventStatus.CANCELLED:
            raise BadRequest("Cancel the event's shows instead (PUT /shows/{id}/cancel)")
    if "duration_minutes" in changes and changes["duration_minutes"] is not None:
        has_shows = db.scalar(select(func.count()).select_from(Show).where(Show.event_id == event_id))
        if has_shows and changes["duration_minutes"] != event.duration_minutes:
            raise BadRequest("Duration cannot change after shows have been scheduled")
    for field, value in changes.items():
        if value is None:
            continue
        setattr(event, field, value.value if hasattr(value, "value") else value)
    db.commit()
    cache_service.invalidate_event_cache()
    return event


# ------------------------------------------------------------------- shows
def screen_categories(db: Session, screen_id: int) -> set:
    """Seat categories that exist (active seats) on the screen."""
    rows = db.scalars(select(Seat.category).where(Seat.screen_id == screen_id, Seat.is_active.is_(True)).distinct())
    return set(rows.all())


def _validate_prices(db: Session, screen_id: int, prices: Dict[SeatCategory, Decimal]) -> Dict[str, Decimal]:
    available = screen_categories(db, screen_id)
    if not available:
        raise BadRequest("The screen has no active seats yet; add seats before scheduling shows")
    given = {c.value: Decimal(p) for c, p in prices.items()}
    missing = sorted(available - set(given))
    if missing:
        raise BadRequest(f"A price is required for every seat category on the screen. Missing: {', '.join(missing)}")
    extra = sorted(set(given) - available)
    if extra:
        raise BadRequest(f"The screen has no {', '.join(extra)} seats, so no price can be set for them")
    return given


def _times(event: Event, show_date: date, start_time) -> tuple:
    start_at = datetime.combine(show_date, start_time).replace(tzinfo=None)
    end_at = start_at + timedelta(minutes=event.duration_minutes)
    closes_at = start_at - timedelta(minutes=settings.BOOKING_CLOSE_MINUTES)
    return start_at, end_at, closes_at


def _assert_no_overlap(db: Session, screen_id: int, start_at, end_at, exclude_show_id: Optional[int] = None) -> None:
    """Shows on one screen must be at least CLEANING_GAP_MINUTES apart."""
    gap = timedelta(minutes=settings.CLEANING_GAP_MINUTES)
    stmt = select(Show.id).where(
        Show.screen_id == screen_id,
        Show.status != ShowStatus.CANCELLED.value,
        Show.start_at < end_at + gap,
        Show.end_at > start_at - gap,
    )
    if exclude_show_id:
        stmt = stmt.where(Show.id != exclude_show_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise Conflict(
            f"Another show on this screen overlaps this time (a {settings.CLEANING_GAP_MINUTES}-minute cleaning gap is required)"
        )


def create_show(db: Session, user: User, event_id: int, data: ShowCreate) -> Show:
    event = get_event_or_404(db, event_id)
    assert_can_manage_event(user, event)
    if event.status in (EventStatus.CANCELLED.value, EventStatus.COMPLETED.value):
        raise BadRequest(f"Cannot add shows to a {event.status} event")
    screen = db.get(Screen, data.screen_id)
    if screen is None:
        raise NotFound("Screen not found")
    venue = db.get(Venue, screen.venue_id)
    if not venue.is_active:
        raise BadRequest("The venue is inactive")
    start_at, end_at, closes_at = _times(event, data.show_date, data.start_time)
    if start_at <= utc_now():
        raise BadRequest("Show date and time must be in the future (UTC)")
    prices = _validate_prices(db, screen.id, data.prices)
    _assert_no_overlap(db, screen.id, start_at, end_at)

    show = Show(
        event_id=event.id,
        screen_id=screen.id,
        show_date=data.show_date,
        start_time=data.start_time,
        start_at=start_at,
        end_at=end_at,
        booking_closes_at=closes_at,
        status=ShowStatus.SCHEDULED.value,
    )
    show.prices = [ShowPrice(category=c, price=p) for c, p in prices.items()]
    db.add(show)
    db.commit()
    cache_service.invalidate_event_cache()
    return show


def get_show_or_404(db: Session, show_id: int) -> Show:
    show = db.get(Show, show_id)
    if show is None:
        raise NotFound("Show not found")
    return show


def has_confirmed_bookings(db: Session, show_id: int) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.show_id == show_id, Booking.status == BookingStatus.CONFIRMED.value)
        )
        > 0
    )


def update_show(db: Session, user: User, show_id: int, data: ShowUpdate) -> Show:
    show = get_show_or_404(db, show_id)
    event = get_event_or_404(db, show.event_id)
    assert_can_manage_event(user, event)
    if show.status != ShowStatus.SCHEDULED.value:
        raise BadRequest(f"A {show.status} show cannot be modified")
    changes = data.model_dump(exclude_unset=True)
    confirmed = has_confirmed_bookings(db, show.id)

    wants_reschedule = any(changes.get(k) is not None for k in ("screen_id", "show_date", "start_time"))
    if confirmed and data.prices is not None:
        raise Conflict("Prices cannot be changed after the first confirmed booking")
    if confirmed and wants_reschedule:
        raise Conflict("The show cannot be rescheduled or moved after a confirmed booking exists; cancel it instead")

    screen_id = changes.get("screen_id") or show.screen_id
    show_date = changes.get("show_date") or show.show_date
    start_time = changes.get("start_time") or show.start_time
    if screen_id != show.screen_id:
        screen = db.get(Screen, screen_id)
        if screen is None:
            raise NotFound("Screen not found")
    start_at, end_at, closes_at = _times(event, show_date, start_time)
    if wants_reschedule:
        if start_at <= utc_now():
            raise BadRequest("Show date and time must be in the future (UTC)")
        _assert_no_overlap(db, screen_id, start_at, end_at, exclude_show_id=show.id)

    # Prices must cover the (possibly new) screen's categories.
    new_prices = None
    if data.prices is not None or screen_id != show.screen_id:
        price_input = data.prices
        if price_input is None:
            raise BadRequest("Provide prices for the new screen's seat categories when changing the screen")
        new_prices = _validate_prices(db, screen_id, price_input)

    show.screen_id, show.show_date, show.start_time = screen_id, show_date, start_time
    show.start_at, show.end_at, show.booking_closes_at = start_at, end_at, closes_at
    if new_prices is not None:
        show.prices.clear()
        db.flush()
        show.prices.extend(ShowPrice(category=c, price=p) for c, p in new_prices.items())
    db.commit()
    cache_service.invalidate_event_cache()
    return show


def show_to_out(db: Session, show: Show) -> ShowOut:
    screen = db.get(Screen, show.screen_id)
    venue = db.get(Venue, screen.venue_id) if screen else None
    out = ShowOut(
        id=show.id,
        event_id=show.event_id,
        screen_id=show.screen_id,
        show_date=show.show_date,
        start_time=show.start_time,
        start_at=show.start_at,
        end_at=show.end_at,
        booking_closes_at=show.booking_closes_at,
        status=show.status,
        prices={p.category: p.price for p in show.prices},
    )
    out.prices = {p.category: p.price for p in show.prices}
    if screen and venue:
        out.screen_name, out.venue_id, out.venue_name, out.city = screen.name, venue.id, venue.name, venue.city
    return out


def list_shows(db: Session, user: Optional[User], event_id: int):
    event = get_event_or_404(db, event_id)
    if not can_view_event(user, event):
        raise NotFound("Event not found")
    shows = db.scalars(select(Show).where(Show.event_id == event_id).order_by(Show.start_at)).all()
    if user is None or user.role in (Role.CUSTOMER.value, Role.STAFF.value):
        # Customers do not need cancelled shows cluttering the list.
        shows = [s for s in shows if s.status != ShowStatus.CANCELLED.value]
    return [show_to_out(db, s) for s in shows]


# --------------------------------------------------------------- listing
SORT_FIELDS = {"show_date", "price", "title"}


def list_events(
    db: Session,
    user: Optional[User],
    p: Pagination,
    title: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    sort_by: str = "show_date",
    order: str = "asc",
) -> dict:
    if sort_by not in SORT_FIELDS:
        raise BadRequest("sort_by must be one of: show_date, price, title")
    if order not in ("asc", "desc"):
        raise BadRequest("order must be 'asc' or 'desc'")
    if from_date and to_date and from_date > to_date:
        raise BadRequest("from_date must not be after to_date")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise BadRequest("min_price must not exceed max_price")

    # Who is asking decides what they may see (and therefore the cache key).
    if user is not None and user.role == Role.ADMIN.value:
        scope = "admin"
    elif user is not None and user.role == Role.ORGANIZER.value:
        scope = f"org{user.id}"
    else:
        scope = "public"

    cache_key = "list:" + "|".join(
        str(x)
        for x in (scope, title, city, category, language, from_date, to_date, min_price, max_price, sort_by, order, p.skip, p.limit)
    )
    cached = cache_service.cache_get(cache_key)
    if cached is not None:
        return cached

    # Sub-query: matching shows aggregated per event (next show time and cheapest price).
    show_stmt = (
        select(
            Show.event_id.label("event_id"),
            func.min(Show.start_at).label("next_show_at"),
            func.min(ShowPrice.price).label("min_price"),
        )
        .join(ShowPrice, ShowPrice.show_id == Show.id)
        .join(Screen, Screen.id == Show.screen_id)
        .join(Venue, Venue.id == Screen.venue_id)
        .where(Show.status == ShowStatus.SCHEDULED.value)
    )
    show_filtered = False
    if scope == "public":
        show_stmt = show_stmt.where(Show.start_at >= utc_now())
    if city:
        show_stmt = show_stmt.where(func.lower(Venue.city) == city.strip().lower())
        show_filtered = True
    if from_date:
        show_stmt = show_stmt.where(Show.show_date >= from_date)
        show_filtered = True
    if to_date:
        show_stmt = show_stmt.where(Show.show_date <= to_date)
        show_filtered = True
    if min_price is not None:
        show_stmt = show_stmt.where(ShowPrice.price >= min_price)
        show_filtered = True
    if max_price is not None:
        show_stmt = show_stmt.where(ShowPrice.price <= max_price)
        show_filtered = True
    show_sq = show_stmt.group_by(Show.event_id).subquery()

    stmt = select(Event, show_sq.c.next_show_at, show_sq.c.min_price)
    stmt = stmt.join(show_sq, show_sq.c.event_id == Event.id, isouter=not show_filtered)

    if scope == "public":
        stmt = stmt.where(Event.status == EventStatus.PUBLISHED.value)
    elif scope.startswith("org"):
        stmt = stmt.where(Event.organizer_id == user.id)
    if title:
        escaped = title.strip().replace("%", r"\%").replace("_", r"\_")
        stmt = stmt.where(Event.title.ilike(f"%{escaped}%", escape="\\"))
    if category:
        stmt = stmt.where(Event.category == category)
    if language:
        stmt = stmt.where(func.lower(Event.language) == language.strip().lower())

    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))

    direction = asc if order == "asc" else desc
    sort_col = {"show_date": show_sq.c.next_show_at, "price": show_sq.c.min_price, "title": Event.title}[sort_by]
    stmt = stmt.order_by(direction(sort_col), Event.id).offset(p.skip).limit(p.limit)

    items = []
    for event, next_show_at, event_min_price in db.execute(stmt).all():
        item = EventListItem.model_validate(event)
        item.next_show_at, item.min_price = next_show_at, event_min_price
        items.append(item.model_dump(mode="json"))
    result = {"items": items, "total": total, "skip": p.skip, "limit": p.limit}
    cache_service.cache_set(cache_key, result)
    return result


def event_detail(db: Session, user: Optional[User], event_id: int) -> dict:
    event = get_event_or_404(db, event_id)
    if not can_view_event(user, event):
        raise NotFound("Event not found")
    include_cancelled = user is not None and user.role in (Role.ADMIN.value, Role.ORGANIZER.value)
    # Only the public view (no cancelled shows) is cached, and it is shared by everyone who may see it.
    use_cache = event.status == EventStatus.PUBLISHED.value and not include_cancelled
    cache_key = f"detail:{event_id}:public"
    if use_cache:
        cached = cache_service.cache_get(cache_key)
        if cached is not None:
            return cached
    shows = db.scalars(select(Show).where(Show.event_id == event_id).order_by(Show.start_at)).all()
    data = EventOut.model_validate(event).model_dump(mode="json")
    data["shows"] = [
        show_to_out(db, s).model_dump(mode="json")
        for s in shows
        if include_cancelled or s.status != ShowStatus.CANCELLED.value
    ]
    if use_cache:
        cache_service.cache_set(cache_key, data)
    return data
