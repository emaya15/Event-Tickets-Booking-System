from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_customer
from app.database import get_db
from app.models import User
from app.services import razorpay_service

router = APIRouter(tags=["Razorpay (bonus)"])


class CheckoutConfirm(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/bookings/{booking_id}/razorpay-order")
def create_order(booking_id: int, user: User = Depends(require_customer), db: Session = Depends(get_db)):
    """Create a Razorpay test order (amount in paise, derived from the booking)."""
    return razorpay_service.create_order(db, user, booking_id)


@router.post("/bookings/{booking_id}/razorpay-confirm")
def confirm(
    booking_id: int,
    data: CheckoutConfirm,
    background: BackgroundTasks,
    user: User = Depends(require_customer),
    db: Session = Depends(get_db),
):
    result = razorpay_service.confirm_checkout(
        db, user, booking_id, data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature, background
    )
    return {"outcome": result["outcome"]}


@router.post("/webhooks/razorpay")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_razorpay_signature: str = Header(default=None),
    x_razorpay_event_id: str = Header(default=None),
    db: Session = Depends(get_db),
):
    raw = await request.body()  # signature is computed over the exact raw bytes
    return razorpay_service.handle_webhook(db, raw, x_razorpay_signature, x_razorpay_event_id, background)
