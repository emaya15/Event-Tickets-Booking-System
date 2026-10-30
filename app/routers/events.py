from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_optional_user, require_roles
from app.database import get_db
from app.models import User
from app.schemas.event import EventCreate, EventOut, EventUpdate, ShowCreate, ShowOut, ShowUpdate
from app.services import cancellation_service, event_service
from app.utils.constants import EventCategory, Role
from app.utils.pagination import Pagination

router = APIRouter(tags=["Events & Shows"])

organizer_or_admin = require_roles(Role.ORGANIZER, Role.ADMIN)


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(data: EventCreate, user: User = Depends(organizer_or_admin), db: Session = Depends(get_db)):
    """Approved Organizers (or Admins) create events."""
    return event_service.create_event(db, user, data)


@router.get("/events")
def list_events(
    title: Optional[str] = Query(None, description="Partial, case-insensitive title match"),
    city: Optional[str] = None,
    category: Optional[EventCategory] = None,
    language: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    min_price: Optional[Decimal] = Query(None, ge=0),
    max_price: Optional[Decimal] = Query(None, ge=0),
    sort_by: str = Query("show_date", description="show_date | price | title"),
    order: str = Query("asc", description="asc | desc"),
    p: Pagination = Depends(),
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Public event list (Published only). Cached in Redis for 5 minutes."""
    return event_service.list_events(
        db, user, p, title, city, category.value if category else None, language,
        from_date, to_date, min_price, max_price, sort_by, order,
    )


@router.get("/events/{event_id}")
def get_event(event_id: int, user: Optional[User] = Depends(get_optional_user), db: Session = Depends(get_db)):
    return event_service.event_detail(db, user, event_id)


@router.put("/events/{event_id}", response_model=EventOut)
def update_event(event_id: int, data: EventUpdate, user: User = Depends(organizer_or_admin), db: Session = Depends(get_db)):
    return event_service.update_event(db, user, event_id, data)


@router.post("/events/{event_id}/shows", response_model=ShowOut, status_code=201)
def create_show(event_id: int, data: ShowCreate, user: User = Depends(organizer_or_admin), db: Session = Depends(get_db)):
    show = event_service.create_show(db, user, event_id, data)
    return event_service.show_to_out(db, show)


@router.get("/events/{event_id}/shows", response_model=List[ShowOut])
def list_shows(event_id: int, user: Optional[User] = Depends(get_optional_user), db: Session = Depends(get_db)):
    return event_service.list_shows(db, user, event_id)


@router.put("/shows/{show_id}", response_model=ShowOut)
def update_show(show_id: int, data: ShowUpdate, user: User = Depends(organizer_or_admin), db: Session = Depends(get_db)):
    show = event_service.update_show(db, user, show_id, data)
    return event_service.show_to_out(db, show)


@router.put("/shows/{show_id}/cancel", response_model=ShowOut)
def cancel_show(
    show_id: int,
    background: BackgroundTasks,
    user: User = Depends(organizer_or_admin),
    db: Session = Depends(get_db),
):
    """Cancels the show, refunds every confirmed booking in full and notifies customers."""
    show = cancellation_service.cancel_show(db, user, show_id, background)
    return event_service.show_to_out(db, show)
