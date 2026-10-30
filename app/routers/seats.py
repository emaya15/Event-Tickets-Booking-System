from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_customer
from app.database import get_db
from app.models import User
from app.schemas.seat import SeatRelease, SeatSelection
from app.services import event_service, seat_service, waitlist_service
from app.utils.constants import SeatCategory, SeatState
from app.utils.errors import NotFound
from app.utils.rate_limit import lock_rate_limit
from app.websockets.manager import manager

router = APIRouter(tags=["Seats & Locking"])


def _visible_show(db: Session, user: User, show_id: int):
    show = event_service.get_show_or_404(db, show_id)
    event = event_service.get_event_or_404(db, show.event_id)
    if not event_service.can_view_event(user, event):
        raise NotFound("Show not found")
    return show


@router.get("/shows/{show_id}/seats")
def seat_map(show_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Live seat map (never cached): Available / Locked / Booked / Unavailable + category and price."""
    show = _visible_show(db, user, show_id)
    return seat_service.seat_map_for_viewer(db, show, user.id)


@router.get("/shows/{show_id}/suggest-seats")
def suggest_seats(
    show_id: int,
    count: int = Query(..., ge=1, le=10),
    category: Optional[SeatCategory] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bonus: best adjacent seats for `count` people (respects locks, bookings and the single-seat rule)."""
    show = _visible_show(db, user, show_id)
    return seat_service.suggest_seats(db, show, count, category.value if category else None)


@router.post("/shows/{show_id}/lock-seats")
def lock_seats(
    show_id: int,
    data: SeatSelection,
    user: User = Depends(lock_rate_limit),
    db: Session = Depends(get_db),
):
    """All-or-nothing 10-minute lock. Customers only; max 5 requests/minute."""
    from app.utils.errors import Forbidden
    from app.utils.constants import Role

    if user.role != Role.CUSTOMER.value:
        raise Forbidden("Only customers can lock seats")
    show = _visible_show(db, user, show_id)
    result = seat_service.lock_seats(db, show, user, data.seat_ids)
    manager.publish_seats(show.id, "locked", result["newly_locked_seat_ids"], SeatState.LOCKED.value, owner_id=user.id)
    return result


@router.delete("/shows/{show_id}/lock-seats")
def release_seats(
    show_id: int,
    data: Optional[SeatRelease] = Body(default=None),
    user: User = Depends(require_customer),
    db: Session = Depends(get_db),
):
    """Release your own locks (all of them if no seat_ids are supplied)."""
    show = event_service.get_show_or_404(db, show_id)
    result = seat_service.release_seats(db, show, user, data.seat_ids if data else None)
    manager.publish_seats(show.id, "released", result["released_seat_ids"], SeatState.AVAILABLE.value)
    waitlist_service.process_waitlist(db, show.id)
    return result
