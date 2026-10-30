from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Money, ORMModel
from app.utils.constants import PaymentMethod, SimulateStatus


class BookingCreate(BaseModel):
    show_id: int
    seat_ids: List[int] = Field(min_length=1, max_length=10)
    coupon_code: Optional[str] = Field(default=None, max_length=40)

    @field_validator("seat_ids")
    @classmethod
    def _unique(cls, ids):
        if len(set(ids)) != len(ids):
            raise ValueError("seat_ids must not contain duplicates")
        return ids


class BookedSeatOut(BaseModel):
    seat_id: int
    row_label: str
    seat_number: int
    category: str
    price: Money


class BookingOut(ORMModel):
    id: int
    booking_number: str
    user_id: int
    show_id: int
    seat_count: int
    ticket_total: Money
    discount_amount: Money
    convenience_fee: Money
    gst_amount: Money
    grand_total: Money
    coupon_code: Optional[str] = None
    status: str
    expires_at: datetime
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    seats: List[BookedSeatOut] = []
    event_title: Optional[str] = None
    show_start_at: Optional[datetime] = None
    venue_name: Optional[str] = None
    screen_name: Optional[str] = None
    customer_name: Optional[str] = None


class PayRequest(BaseModel):
    payment_method: PaymentMethod
    simulate_status: SimulateStatus = Field(
        default=SimulateStatus.SUCCESS, description="TESTING ONLY: force the mock gateway result"
    )


class PaymentOut(ORMModel):
    id: int
    booking_id: int
    transaction_id: str
    amount: Money
    payment_method: str
    status: str
    paid_at: datetime
    gateway: str


class RefundOut(ORMModel):
    id: int
    booking_id: int
    amount: Money
    reason: str
    status: str
    refunded_at: datetime


class CancelResult(BaseModel):
    booking: BookingOut
    refund: Optional[RefundOut] = None
    refund_amount: Money
    message: str


class WaitlistCreate(BaseModel):
    seats_requested: int = Field(ge=1, le=10)
    preferred_category: Optional[str] = Field(default=None, pattern="^(Silver|Gold|Recliner)$")


class WaitlistOut(ORMModel):
    id: int
    show_id: int
    user_id: int
    seats_requested: int
    preferred_category: Optional[str] = None
    status: str
    notified_at: Optional[datetime] = None
    created_at: datetime
    position: Optional[int] = None


class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    discount_type: str = Field(pattern="^(Percent|Flat)$")
    discount_value: float = Field(gt=0)
    min_tickets: int = Field(default=1, ge=1, le=10)
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = Field(default=None, ge=1)

    @field_validator("discount_value")
    @classmethod
    def _round(cls, v):
        return round(v, 2)


class CouponOut(ORMModel):
    id: int
    code: str
    discount_type: str
    discount_value: Money
    min_tickets: int
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = None
    used_count: int
    is_active: bool
