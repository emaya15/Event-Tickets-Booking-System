from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_staff
from app.database import get_db
from app.models import User
from app.services import ticket_service

router = APIRouter(tags=["Tickets & Entry"])


class ScanRequest(BaseModel):
    qr_data: str = Field(min_length=10, max_length=300, description="Text decoded from the ticket QR code")


@router.post("/tickets/scan")
def scan(data: ScanRequest, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Staff only. Verifies the HMAC signature, venue, time window and one-time use."""
    return ticket_service.scan_ticket(db, staff, data.qr_data)


@router.get("/shows/{show_id}/entries")
def entries(show_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Entered customers vs total confirmed bookings (Staff of the venue, owning Organizer, Admin)."""
    return ticket_service.entries_report(db, user, show_id)
