"""Seat states, Redis seat locks, the single-empty-seat rule and dynamic pricing.

Redis lock key  : lock:show:<show_id>:seat:<seat_id>
Redis lock value: "<user_id>:<locked_price>"   (owner + the price shown when the seat was locked)
Every lock is created with SET NX EX 600, so it is atomic and expires on its own.
"""
import logging
import time as _time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BookedSeat, Booking, Seat, Show, ShowPrice, User
from app.redis_client import get_redis
from app.utils.constants import BookingStatus, SeatState, ShowStatus
from app.utils.errors import BadRequest, Conflict, NotFound
from app.utils.time import utc_now

logger = logging.getLogger("app.seats")

TWO = Decimal("0.01")
SURGE_THRESHOLD = Decimal("0.80")
SURGE_MULTIPLIER = Decimal("1.10")


# ------------------------------------------------------------ redis helpers
def lock_key(show_id: int, seat_id: int) -> str:
    return f"lock:show:{show_id}:seat:{seat_id}"


def encode_lock(user_id: int, price: Decimal) -> str:
    return f"{user_id}:{Decimal(price).quantize(TWO)}"


def decode_lock(value: Optional[str]) -> Optional[Tuple[int, Decimal]]:
    if not value:
        return None
    try:
        owner, price = value.split(":", 1)
        return int(owner), Decimal(price)
    except (ValueError, ArithmeticError):
        return None


_RELEASE_LUA = """
local released = 0
for i, key in ipairs(KEYS) do
  local v = redis.call('GET', key)
  if v and string.sub(v, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. ':' then
    redis.call('DEL', key)
    released = released + 1
  end
end
return released
"""


def release_locks_owned_by(user_id: int, keys: List[str]) -> int:
    """Delete only the keys still owned by ``user_id`` (compare-and-delete, atomic via Lua)."""
    if not keys:
        return 0
    r = get_redis()
    try:
        return int(r.eval(_RELEASE_LUA, len(keys), *keys, str(user_id)))
    except Exception:  # Lua unavailable (e.g. minimal fake Redis) - fall back to a best-effort loop
        released = 0
        for key in keys:
            parsed = decode_lock(r.get(key))
            if parsed and parsed[0] == user_id:
                released += r.delete(key)
        return released


@contextmanager
def show_mutex(show_id: int, wait_seconds: float = 3.0):
    """Serialises seat-lock operations on one show (the single-seat rule needs a consistent view)."""
    r = get_redis()
    key = f"mutex:show:{show_id}"
    token = uuid.uuid4().hex
    deadline = _time.monotonic() + wait_seconds
    while not r.set(key, token, nx=True, ex=5):
        if _time.monotonic() > deadline:
            raise Conflict("Seat selection is busy for this show, please retry in a moment")
        _time.sleep(0.03)
    try:
        yield
    finally:
        try:
            if r.get(key) == token:
                r.delete(key)
        except Exception:  # pragma: no cover
            pass


# --------------------------------------------------------------- show state
def assert_show_bookable(show: Show) -> None:
    if show.status != ShowStatus.SCHEDULED.value:
        raise BadRequest(f"This show is {show.status} and cannot be booked")
    if utc_now() >= show.booking_closes_at:
        raise BadRequest("Booking for this show has closed")


def base_prices(db: Session, show_id: int) -> Dict[str, Decimal]:
    return {p.category: p.price for p in db.scalars(select(ShowPrice).where(ShowPrice.show_id == show_id))}


def booked_seat_ids(db: Session, show_id: int) -> set:
    rows = db.scalars(
        select(BookedSeat.seat_id)
        .join(Booking, Booking.id == BookedSeat.booking_id)
        .where(BookedSeat.show_id == show_id, Booking.status == BookingStatus.CONFIRMED.value)
    )
    return set(rows.all())


def surge_active(total_active_seats: int, booked_count: int) -> bool:
    """Dynamic pricing: +10% once MORE than 80% of the active seats are booked."""
    if not settings.DYNAMIC_PRICING_ENABLED or total_active_seats <= 0:
        return False
    return Decimal(booked_count) / Decimal(total_active_seats) > SURGE_THRESHOLD


def effective_price(base: Decimal, surge: bool) -> Decimal:
    price = Decimal(base)
    if surge:
        price = price * SURGE_MULTIPLIER
    return price.quantize(TWO, rounding=ROUND_HALF_UP)


def compute_seat_states(db: Session, show: Show) -> List[dict]:
    """Full seat map for a show (Available / Locked / Booked / Unavailable) with prices.

    Never cached: it is rebuilt from the database and Redis on every call.
    """
    seats = db.scalars(
        select(Seat).where(Seat.screen_id == show.screen_id).order_by(Seat.row_label, Seat.seat_number)
    ).all()
    booked = booked_seat_ids(db, show.id)
    prices = base_prices(db, show.id)
    active_total = sum(1 for s in seats if s.is_active)
    surge = surge_active(active_total, len(booked & {s.id for s in seats if s.is_active}))

    keys = [lock_key(show.id, s.id) for s in seats]
    values = get_redis().mget(keys) if keys else []

    result = []
    for seat, raw in zip(seats, values):
        lock = decode_lock(raw)
        if not seat.is_active:
            status, owner = SeatState.UNAVAILABLE.value, None
        elif seat.id in booked:
            status, owner = SeatState.BOOKED.value, None
        elif lock:
            status, owner = SeatState.LOCKED.value, lock[0]
        else:
            status, owner = SeatState.AVAILABLE.value, None
        base = prices.get(seat.category)
        result.append(
            {
                "seat_id": seat.id,
                "row_label": seat.row_label,
                "seat_number": seat.seat_number,
                "category": seat.category,
                "price": float(effective_price(base, surge)) if base is not None else None,
                "status": status,
                "owner_id": owner,
            }
        )
    return result


def seat_map_for_viewer(db: Session, show: Show, viewer_id: Optional[int], mark_yours: bool = False) -> dict:
    states = compute_seat_states(db, show)
    seats = []
    for s in states:
        item = {k: v for k, v in s.items() if k != "owner_id"}
        mine = s["status"] == SeatState.LOCKED.value and viewer_id is not None and s["owner_id"] == viewer_id
        item["locked_by_you"] = mine
        if mine and mark_yours:
            item["status"] = SeatState.YOURS.value
        seats.append(item)
    counts: Dict[str, int] = {}
    for s in states:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {"show_id": show.id, "status": show.status, "counts": counts, "seats": seats}


# -------------------------------------------------- single-empty-seat rule
def _isolated_single_seats(rows: Dict[str, List[Tuple[int, bool]]]) -> set:
    """Return {(row, seat_number)} of available seats whose two neighbours are both taken.

    ``rows`` maps row label -> list of (seat_number, taken) for ACTIVE seats ordered by number.
    Neighbours must have consecutive seat numbers; row ends and numbering gaps (aisles) count as open.
    """
    isolated = set()
    for row, seats in rows.items():
        by_number = dict(seats)
        for number, taken in seats:
            if taken:
                continue
            left, right = by_number.get(number - 1), by_number.get(number + 1)
            if left is True and right is True:
                isolated.add((row, number))
    return isolated


def check_single_seat_rule(
    db: Session, show: Show, new_seat_ids: Iterable[int], taken_before: set
) -> None:
    """Raise 409 if locking ``new_seat_ids`` would strand a single available seat between taken seats."""
    new_ids = set(new_seat_ids)
    seats = db.scalars(
        select(Seat).where(Seat.screen_id == show.screen_id, Seat.is_active.is_(True)).order_by(Seat.row_label, Seat.seat_number)
    ).all()
    before: Dict[str, List[Tuple[int, bool]]] = {}
    after: Dict[str, List[Tuple[int, bool]]] = {}
    for seat in seats:
        was_taken = seat.id in taken_before
        before.setdefault(seat.row_label, []).append((seat.seat_number, was_taken))
        after.setdefault(seat.row_label, []).append((seat.seat_number, was_taken or seat.id in new_ids))
    created = _isolated_single_seats(after) - _isolated_single_seats(before)
    if created:
        labels = sorted(f"{row}{number}" for row, number in created)
        raise Conflict(
            "This selection would leave a single empty seat between taken seats: " + ", ".join(labels),
            extra={"stranded_seats": labels},
        )


def taken_seat_ids(db: Session, show: Show) -> set:
    """Seats that are booked or locked by anybody right now."""
    seat_ids = db.scalars(select(Seat.id).where(Seat.screen_id == show.screen_id)).all()
    taken = set(booked_seat_ids(db, show.id))
    if seat_ids:
        values = get_redis().mget([lock_key(show.id, sid) for sid in seat_ids])
        taken |= {sid for sid, raw in zip(seat_ids, values) if raw}
    return taken


# ------------------------------------------------------------------ locking
def my_locks(show_id: int, user_id: int, seat_ids: Iterable[int]) -> Dict[int, Tuple[Decimal, int]]:
    """{seat_id: (locked_price, ttl_seconds)} for seats currently locked by this user."""
    seat_ids = list(seat_ids)
    if not seat_ids:
        return {}
    r = get_redis()
    keys = [lock_key(show_id, sid) for sid in seat_ids]
    values = r.mget(keys)
    mine = {}
    for sid, key, raw in zip(seat_ids, keys, values):
        parsed = decode_lock(raw)
        if parsed and parsed[0] == user_id:
            ttl = r.ttl(key)
            if ttl and ttl > 0:
                mine[sid] = (parsed[1], int(ttl))
    return mine


def all_my_locked_seat_ids(db: Session, show: Show, user_id: int) -> List[int]:
    seat_ids = db.scalars(select(Seat.id).where(Seat.screen_id == show.screen_id)).all()
    return sorted(my_locks(show.id, user_id, seat_ids).keys())


def seats_held_or_booked_by_user(db: Session, show: Show, user_id: int) -> int:
    """Confirmed seats of this user for the show (locks are counted separately)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(BookedSeat)
            .join(Booking, Booking.id == BookedSeat.booking_id)
            .where(
                BookedSeat.show_id == show.id,
                Booking.user_id == user_id,
                Booking.status == BookingStatus.CONFIRMED.value,
            )
        )
        or 0
    )


def lock_seats(db: Session, show: Show, user: User, seat_ids: List[int]) -> dict:
    """All-or-nothing seat lock. Returns the lock summary or raises 400/409."""
    seat_ids = list(dict.fromkeys(seat_ids))  # de-duplicate, keep order
    if not seat_ids:
        raise BadRequest("seat_ids must not be empty")
    assert_show_bookable(show)

    seats = db.scalars(select(Seat).where(Seat.id.in_(seat_ids))).all()
    found = {s.id: s for s in seats}
    missing = [sid for sid in seat_ids if sid not in found or found[sid].screen_id != show.screen_id]
    if missing:
        raise BadRequest(f"Seats not found on this show's screen: {missing}")

    with show_mutex(show.id):
        r = get_redis()
        prices = base_prices(db, show.id)
        booked = booked_seat_ids(db, show.id)
        active_total = db.scalar(
            select(func.count()).select_from(Seat).where(Seat.screen_id == show.screen_id, Seat.is_active.is_(True))
        )
        surge = surge_active(active_total, len(booked))

        unavailable = []
        for sid in seat_ids:
            if not found[sid].is_active:
                unavailable.append({"seat_id": sid, "reason": "Unavailable"})
            elif sid in booked:
                unavailable.append({"seat_id": sid, "reason": "Booked"})
        already_mine = my_locks(show.id, user.id, seat_ids)
        for sid in seat_ids:
            if sid in already_mine or any(u["seat_id"] == sid for u in unavailable):
                continue
            existing = decode_lock(r.get(lock_key(show.id, sid)))
            if existing:
                unavailable.append({"seat_id": sid, "reason": "Locked"})
        if unavailable:
            raise Conflict("Some seats are not available", extra={"unavailable_seats": unavailable})

        # --- per-show limit: seats already confirmed + all of my current locks + the new ones
        mine_all = set(all_my_locked_seat_ids(db, show, user.id))
        total_after = seats_held_or_booked_by_user(db, show, user.id) + len(mine_all | set(seat_ids))
        if total_after > settings.MAX_SEATS_PER_SHOW:
            raise BadRequest(f"You can lock or book at most {settings.MAX_SEATS_PER_SHOW} seats per show")

        new_ids = [sid for sid in seat_ids if sid not in already_mine]
        # --- single-empty-seat rule (compared against the state before this request)
        check_single_seat_rule(db, show, new_ids, taken_seat_ids(db, show))

        # --- atomic acquisition: SET NX EX per seat; roll back everything on the first failure
        acquired: List[str] = []
        failed: List[dict] = []
        for sid in new_ids:
            category_price = prices.get(found[sid].category)
            if category_price is None:
                failed.append({"seat_id": sid, "reason": "No price configured"})
                break
            value = encode_lock(user.id, effective_price(category_price, surge))
            key = lock_key(show.id, sid)
            if r.set(key, value, nx=True, ex=settings.SEAT_LOCK_SECONDS):
                acquired.append(key)
            else:
                failed.append({"seat_id": sid, "reason": "Locked"})
                break
        if failed:
            release_locks_owned_by(user.id, acquired)
            raise Conflict("Some seats are not available", extra={"unavailable_seats": failed})

        locked_now = my_locks(show.id, user.id, seat_ids)

    expires_in = min((ttl for _, ttl in locked_now.values()), default=settings.SEAT_LOCK_SECONDS)
    return {
        "show_id": show.id,
        "locked_seat_ids": seat_ids,
        "newly_locked_seat_ids": new_ids,
        "expires_in_seconds": expires_in,
        "expires_at": (utc_now() + timedelta(seconds=expires_in)).isoformat() + "Z",
    }


def release_seats(db: Session, show: Show, user: User, seat_ids: Optional[List[int]]) -> dict:
    """Release the caller's own locks (all of them when ``seat_ids`` is omitted)."""
    mine = all_my_locked_seat_ids(db, show, user.id)
    if seat_ids:
        seat_ids = list(dict.fromkeys(seat_ids))
        not_mine = [sid for sid in seat_ids if sid not in mine]
        if not_mine:
            raise Conflict("You can release only seats you have locked", extra={"not_locked_by_you": not_mine})
        target = seat_ids
    else:
        target = mine

    pending_seats = set(
        db.scalars(
            select(BookedSeat.seat_id)
            .join(Booking, Booking.id == BookedSeat.booking_id)
            .where(
                BookedSeat.show_id == show.id,
                Booking.user_id == user.id,
                Booking.status == BookingStatus.PENDING.value,
            )
        )
    )
    blocked = sorted(set(target) & pending_seats)
    if blocked:
        raise Conflict(
            "These seats belong to a booking awaiting payment; cancel that booking instead",
            extra={"seats_in_pending_booking": blocked},
        )
    released = release_locks_owned_by(user.id, [lock_key(show.id, sid) for sid in target])
    return {"show_id": show.id, "released_seat_ids": target, "released_count": released}


def verify_locks_owned(show_id: int, user_id: int, seat_ids: Iterable[int]) -> Dict[int, Tuple[Decimal, int]]:
    """Raise 409 unless every seat is still locked by ``user_id``. Returns {seat_id: (price, ttl)}."""
    seat_ids = list(seat_ids)
    mine = my_locks(show_id, user_id, seat_ids)
    lost = [sid for sid in seat_ids if sid not in mine]
    if lost:
        raise Conflict("Seat locks are missing or expired", extra={"seats_without_lock": lost})
    return mine


# ------------------------------------------------------- best-seat suggestion
def suggest_seats(db: Session, show: Show, count: int, category: Optional[str], limit: int = 3) -> dict:
    """Suggest up to ``limit`` blocks of ``count`` adjacent seats.

    A block must be consecutive seat numbers in one row, all Available (active, not locked, not booked),
    of the requested category, and must not create a stranded single seat. Blocks closer to the
    middle of the middle rows score best.
    """
    assert_show_bookable(show)
    states = compute_seat_states(db, show)
    rows: Dict[str, list] = {}
    for s in states:
        rows.setdefault(s["row_label"], []).append(s)
    row_names = sorted(rows, key=lambda r: (len(r), r))
    taken = {s["seat_id"] for s in states if s["status"] in (SeatState.LOCKED.value, SeatState.BOOKED.value)}

    # Rows with active seats only, for the stranded-seat check.
    active_rows: Dict[str, List[Tuple[int, bool]]] = {}
    for s in states:
        if s["status"] != SeatState.UNAVAILABLE.value:
            active_rows.setdefault(s["row_label"], []).append((s["seat_number"], s["seat_id"] in taken))
    base_isolated = _isolated_single_seats(active_rows)

    candidates = []
    middle_row = (len(row_names) - 1) / 2
    for r_index, row in enumerate(row_names):
        seats = sorted(rows[row], key=lambda x: x["seat_number"])
        for i in range(len(seats) - count + 1):
            block = seats[i : i + count]
            numbers = [b["seat_number"] for b in block]
            if numbers != list(range(numbers[0], numbers[0] + count)):
                continue
            if any(b["status"] != SeatState.AVAILABLE.value for b in block):
                continue
            if category and any(b["category"] != category for b in block):
                continue
            if len({b["category"] for b in block}) > 1:
                continue
            block_ids = {b["seat_id"] for b in block}
            hypothetical = {
                r: [(n, t or (r == row and n in numbers)) for n, t in lst] for r, lst in active_rows.items()
            }
            if _isolated_single_seats(hypothetical) - base_isolated:
                continue
            row_center = (min(x["seat_number"] for x in seats) + max(x["seat_number"] for x in seats)) / 2
            block_center = (numbers[0] + numbers[-1]) / 2
            score = abs(r_index - middle_row) * 2 + abs(block_center - row_center)
            candidates.append((score, row, block))
    candidates.sort(key=lambda c: (c[0], c[1], c[2][0]["seat_number"]))
    suggestions = [
        {
            "row_label": row,
            "seat_ids": [b["seat_id"] for b in block],
            "seats": [f"{row}{b['seat_number']}" for b in block],
            "category": block[0]["category"],
            "total_price": round(sum(b["price"] or 0 for b in block), 2),
        }
        for _, row, block in candidates[:limit]
    ]
    return {"show_id": show.id, "requested": count, "suggestions": suggestions}
