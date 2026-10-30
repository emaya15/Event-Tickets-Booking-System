"""Optional Razorpay TEST-mode integration (bonus). Bookings are confirmed ONLY from verified payments."""
import hashlib
import hmac
import json
import logging
from typing import Optional

import httpx
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, Payment, WebhookEvent
from app.services import email_service, payment_service
from app.utils.constants import BookingStatus, PaymentMethod, PaymentStatus
from app.utils.errors import BadRequest, Conflict, NotFound, ServiceUnavailable, Unauthorized
from app.utils.time import utc_now

logger = logging.getLogger("app.razorpay")


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def create_order(db: Session, user, booking_id: int) -> dict:
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        raise ServiceUnavailable("Razorpay is not configured")
    booking = db.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise NotFound("Booking not found")
    if booking.status != BookingStatus.PENDING.value:
        raise Conflict(f"Booking is {booking.status}; an order can only be created for Pending Payment bookings")
    amount_paise = int((booking.grand_total * 100).to_integral_value())
    try:
        resp = httpx.post(
            "https://api.razorpay.com/v1/orders",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={"amount": amount_paise, "currency": "INR", "receipt": booking.booking_number},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Razorpay order creation failed")
        raise ServiceUnavailable("Could not create the payment order, please try again")
    order = resp.json()
    booking.razorpay_order_id = order["id"]
    db.commit()
    return {"order_id": order["id"], "amount_paise": amount_paise, "currency": "INR", "key_id": settings.RAZORPAY_KEY_ID}


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature or "")


def confirm_checkout(db: Session, user, booking_id: int, order_id: str, payment_id: str, signature: str, background: BackgroundTasks):
    """Client-side callback: verify the signature, then settle exactly like a mock success."""
    if not settings.RAZORPAY_KEY_SECRET:
        raise ServiceUnavailable("Razorpay is not configured")
    if not verify_checkout_signature(order_id, payment_id, signature):
        raise BadRequest("Invalid payment signature")
    booking = payment_service._lock_booking(db, booking_id)
    if booking is None or booking.user_id != user.id or booking.razorpay_order_id != order_id:
        raise NotFound("Booking not found")
    if booking.status == BookingStatus.CONFIRMED.value:
        raise Conflict("This booking has already been paid and confirmed")
    return payment_service.settle_successful_payment(
        db, booking, PaymentMethod.CARD.value, background, gateway="Razorpay", gateway_payment_id=payment_id
    )


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not settings.RAZORPAY_WEBHOOK_SECRET or not signature:
        return False
    return hmac.compare_digest(_hmac(settings.RAZORPAY_WEBHOOK_SECRET, raw_body), signature)


def handle_webhook(db: Session, raw_body: bytes, signature: Optional[str], event_id: Optional[str], background: BackgroundTasks) -> dict:
    if not verify_webhook_signature(raw_body, signature):
        raise Unauthorized("Invalid webhook signature")
    try:
        payload = json.loads(raw_body)
        event_type = payload["event"]
        entity = payload["payload"]["payment"]["entity"]
    except (ValueError, KeyError, TypeError):
        raise BadRequest("Malformed webhook payload")
    payment_id, order_id = entity.get("id"), entity.get("order_id")
    dedupe_id = event_id or f"{event_type}:{payment_id}"

    # Idempotency: the unique event_id means each webhook is processed once.
    db.add(WebhookEvent(event_id=dedupe_id, event_type=event_type, processed_at=utc_now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate_ignored"}

    booking = db.scalar(select(Booking).where(Booking.razorpay_order_id == order_id)) if order_id else None
    if booking is None:
        return {"status": "ignored", "reason": "unknown order"}
    booking = payment_service._lock_booking(db, booking.id)

    if event_type == "payment.captured":
        if booking.status == BookingStatus.CONFIRMED.value:
            return {"status": "already_confirmed"}
        if booking.status == BookingStatus.CANCELLED.value:
            # Money was captured for a cancelled booking: refund it in full.
            payment, _ = payment_service._refund_late_payment(
                db, booking, PaymentMethod.CARD.value, payment_service.new_transaction_id(), "Razorpay", payment_id
            )
            return {"status": "refunded"}
        # Pending, or Expired (late payment): settle re-checks the seats and confirms or refunds.
        result = payment_service.settle_successful_payment(
            db, booking, PaymentMethod.CARD.value, background, gateway="Razorpay", gateway_payment_id=payment_id
        )
        return {"status": result["outcome"]}
    if event_type == "payment.failed":
        if booking.status == BookingStatus.PENDING.value:
            db.add(
                Payment(
                    booking_id=booking.id,
                    transaction_id=payment_service.new_transaction_id(),
                    amount=booking.grand_total,
                    payment_method=PaymentMethod.CARD.value,
                    status=PaymentStatus.FAILED.value,
                    paid_at=utc_now(),
                    gateway="Razorpay",
                    gateway_payment_id=payment_id,
                )
            )
            db.commit()
        return {"status": "failure_recorded"}
    return {"status": "ignored", "reason": f"unhandled event {event_type}"}
