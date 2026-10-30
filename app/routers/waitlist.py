from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import require_customer, require_roles
from app.database import get_db
from app.models import User
from app.schemas.booking import WaitlistCreate, WaitlistOut
from app.schemas.common import Message
from app.services import waitlist_service
from app.utils.constants import Role
from app.utils.pagination import Pagination, page

router = APIRouter(tags=["Waitlist"])


@router.post("/shows/{show_id}/waitlist", response_model=WaitlistOut, status_code=201)
def join_waitlist(show_id: int, data: WaitlistCreate, user: User = Depends(require_customer), db: Session = Depends(get_db)):
    """Customers join the waitlist of a sold-out show."""
    entry = waitlist_service.join(db, user, show_id, data)
    return waitlist_service.to_out(db, entry)


@router.get("/shows/{show_id}/waitlist")
def list_waitlist(
    show_id: int,
    p: Pagination = Depends(),
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ORGANIZER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Customers see only their own entry; the owning Organizer and Admins see the whole queue."""
    items, total = waitlist_service.list_entries(db, user, show_id, p)
    return page(items, total, p)


@router.delete("/waitlist/{waitlist_id}", response_model=Message)
def leave_waitlist(
    waitlist_id: int,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    waitlist_service.remove(db, user, waitlist_id)
    return Message(message="Removed from the waitlist")
