"""Coupons (bonus): validated on the server; discounts are applied to the ticket total only."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Coupon
from app.schemas.booking import CouponCreate
from app.utils.constants import DiscountType
from app.utils.errors import BadRequest, Conflict, NotFound
from app.utils.time import utc_now

TWO = Decimal("0.01")


def create_coupon(db: Session, data: CouponCreate) -> Coupon:
    code = data.code.upper()
    if db.scalar(select(Coupon.id).where(Coupon.code == code)):
        raise Conflict("Coupon code already exists")
    if data.discount_type == DiscountType.PERCENT.value and data.discount_value > 100:
        raise BadRequest("A percentage discount cannot exceed 100")
    if data.expires_at and data.expires_at <= utc_now():
        raise BadRequest("expires_at must be in the future")
    coupon = Coupon(
        code=code,
        discount_type=data.discount_type,
        discount_value=Decimal(str(data.discount_value)),
        min_tickets=data.min_tickets,
        expires_at=data.expires_at,
        max_uses=data.max_uses,
    )
    db.add(coupon)
    db.commit()
    return coupon


def validate_and_price(db: Session, code: str, ticket_count: int, ticket_total: Decimal) -> Tuple[Coupon, Decimal]:
    """Return (coupon, discount_amount) or raise 400/404 with a clear reason."""
    coupon = db.scalar(select(Coupon).where(Coupon.code == code.strip().upper()))
    if coupon is None or not coupon.is_active:
        raise NotFound("Coupon not found")
    if coupon.expires_at is not None and coupon.expires_at <= utc_now():
        raise BadRequest("Coupon has expired")
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        raise BadRequest("Coupon usage limit reached")
    if ticket_count < coupon.min_tickets:
        raise BadRequest(f"Coupon requires at least {coupon.min_tickets} tickets")
    if coupon.discount_type == DiscountType.PERCENT.value:
        discount = (ticket_total * coupon.discount_value / Decimal(100)).quantize(TWO, rounding=ROUND_HALF_UP)
    else:
        discount = Decimal(coupon.discount_value)
    discount = min(discount, ticket_total)  # never more than the tickets cost
    return coupon, discount.quantize(TWO, rounding=ROUND_HALF_UP)


def list_coupons(db: Session):
    return db.scalars(select(Coupon).order_by(Coupon.id.desc())).all()
