from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_optional_user, require_admin
from app.database import get_db
from app.models import User
from app.schemas.venue import (
    BulkSeatCreate,
    ScreenCreate,
    ScreenOut,
    SeatOut,
    SeatStatusUpdate,
    VenueCreate,
    VenueOut,
    VenueUpdate,
)
from app.services import venue_service
from app.utils.constants import Role
from app.utils.pagination import Pagination, page

router = APIRouter(tags=["Venues, Screens & Seats"])


@router.post("/venues", response_model=VenueOut, status_code=201)
def create_venue(data: VenueCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return venue_service.create_venue(db, data)


@router.get("/venues")
def list_venues(
    city: Optional[str] = Query(None),
    p: Pagination = Depends(),
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Public list of active venues (Admins also see inactive ones)."""
    items, total = venue_service.list_venues(db, p, city, include_inactive=bool(user and user.role == Role.ADMIN.value))
    return page([VenueOut.model_validate(v) for v in items], total, p)


@router.put("/venues/{venue_id}", response_model=VenueOut)
def update_venue(venue_id: int, data: VenueUpdate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return venue_service.update_venue(db, venue_id, data)


@router.post("/venues/{venue_id}/screens", response_model=ScreenOut, status_code=201)
def create_screen(venue_id: int, data: ScreenCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return venue_service.create_screen(db, venue_id, data)


@router.get("/venues/{venue_id}/screens", response_model=List[ScreenOut])
def list_screens(venue_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Helper so organizers can discover screen ids when creating shows."""
    return venue_service.list_screens(db, venue_id)


@router.post("/screens/{screen_id}/seats/bulk", status_code=201)
def bulk_seats(screen_id: int, data: BulkSeatCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return venue_service.bulk_create_seats(db, screen_id, data)


@router.get("/screens/{screen_id}/layout")
def screen_layout(screen_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return venue_service.get_layout(db, screen_id)


@router.put("/seats/{seat_id}/status", response_model=SeatOut)
def seat_status(seat_id: int, data: SeatStatusUpdate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return venue_service.set_seat_active(db, seat_id, data.is_active)
