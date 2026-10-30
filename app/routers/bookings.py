from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_customer, require_roles
from app.database import get_db
from app.models import User
from app.schemas.booking import (
    BookingCreate,
    BookingOut,
    CancelResult,
    PayRequest,
    PaymentOut,
    RefundOut,
)
from app.services import booking_service, cancellation_service, payment_service, ticket_service
from app.utils.constants import BookingStatus, Role
from app.utils.pagination import Pagination, page

router = APIRouter(tags=["Bookings, Payments & Refunds"])


@router.post("/bookings", response_model=BookingOut, status_code=201)
def create_booking(data: BookingCreate, user: User = Depends(require_customer), db: Session = Depends(get_db)):
    """Create a Pending Payment booking from seats you have locked."""
    booking = booking_service.create_booking(db, user, data)
    return booking_service.booking_to_out(db, booking)


@router.get("/bookings")
def list_bookings(
    status: Optional[str] = Query(None, description="Pending Payment | Confirmed | Cancelled | Expired"),
    show_id: Optional[int] = None,
    from_date: Optional[date] = Query(None, description="Booking created on/after (UTC date)"),
    to_date: Optional[date] = Query(None, description="Booking created on/before (UTC date)"),
    p: Pagination = Depends(),
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ORGANIZER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    items, total = booking_service.list_bookings(db, user, p, status, show_id, from_date, to_date)
    return page(items, total, p)


@router.get("/bookings/{booking_id}", response_model=BookingOut)
def get_booking(
    booking_id: int,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ORGANIZER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return booking_service.booking_to_out(db, booking_service.get_booking(db, user, booking_id))


@router.post("/bookings/{booking_id}/pay")
def pay(
    booking_id: int,
    data: PayRequest,
    background: BackgroundTasks,
    user: User = Depends(require_customer),
    db: Session = Depends(get_db),
):
    """Mock payment. The amount is always the booking's grand total; the client cannot set it."""
    from app.utils.errors import Conflict

    result = payment_service.pay_booking(db, user, booking_id, data.payment_method, data.simulate_status, background)
    payment = PaymentOut.model_validate(result["payment"]).model_dump(mode="json")
    booking = booking_service.booking_to_out(db, result["booking"]).model_dump(mode="json")
    if result["outcome"] == "refunded":
        refund = RefundOut.model_validate(result["refund"]).model_dump(mode="json")
        raise Conflict(
            "The seats were taken before your payment completed. A full refund has been issued and the booking cancelled.",
            extra={"payment": payment, "booking": booking, "refund": refund},
        )
    if result["outcome"] == "failed":
        return {"message": "Payment failed. You can retry while the booking is still valid.", "payment": payment, "booking": booking}
    return {"message": "Payment successful. Booking confirmed.", "payment": payment, "booking": booking}


@router.get("/bookings/{booking_id}/payments", response_model=List[PaymentOut])
def booking_payments(
    booking_id: int,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ORGANIZER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return payment_service.list_payments(db, user, booking_id)


@router.get("/bookings/{booking_id}/ticket")
def download_ticket(
    booking_id: int,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Downloads the PDF ticket (Confirmed bookings only)."""
    path = ticket_service.get_ticket_pdf(db, user, booking_id)
    return FileResponse(path, media_type="application/pdf", filename=f"ticket_{booking_id}.pdf")


@router.put("/bookings/{booking_id}/cancel", response_model=CancelResult)
def cancel_booking(
    booking_id: int,
    background: BackgroundTasks,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    result = cancellation_service.cancel_booking(db, user, booking_id, background)
    refund_amount = result["refund_amount"]
    return CancelResult(
        booking=booking_service.booking_to_out(db, result["booking"]),
        refund=RefundOut.model_validate(result["refund"]) if result["refund"] else None,
        refund_amount=refund_amount,
        message=f"Booking cancelled. Refund: Rs. {float(refund_amount):,.2f}",
    )


@router.get("/bookings/{booking_id}/refunds", response_model=List[RefundOut])
def booking_refunds(
    booking_id: int,
    user: User = Depends(require_roles(Role.CUSTOMER, Role.ORGANIZER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return cancellation_service.list_refunds(db, user, booking_id)
