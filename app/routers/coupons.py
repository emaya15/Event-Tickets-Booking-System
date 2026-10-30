from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import User
from app.schemas.booking import CouponCreate, CouponOut
from app.services import coupon_service

router = APIRouter(prefix="/coupons", tags=["Coupons (bonus)"])


@router.post("", response_model=CouponOut, status_code=201)
def create_coupon(data: CouponCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return coupon_service.create_coupon(db, data)


@router.get("", response_model=List[CouponOut])
def list_coupons(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return coupon_service.list_coupons(db)


@router.get("/validate")
def validate_coupon(
    code: str,
    ticket_count: int = Query(..., ge=1, le=10),
    ticket_total: Decimal = Query(..., gt=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview the discount the server would apply to a booking."""
    coupon, discount = coupon_service.validate_and_price(db, code, ticket_count, ticket_total)
    return {"code": coupon.code, "valid": True, "discount_amount": float(discount)}
