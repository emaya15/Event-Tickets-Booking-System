from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, StaffCreateRequest, TokenOut, UserOut
from app.services import auth_service
from app.utils.rate_limit import login_rate_limit

router = APIRouter(tags=["Auth"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Public registration (Customer or Organizer only). Organizers need Admin approval."""
    return auth_service.register_user(db, data)


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(login_rate_limit)])
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Returns a JWT valid for 30 minutes. Rate limited to 5 attempts/minute/IP."""
    return auth_service.authenticate(db, data)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


class ActiveRequest(BaseModel):
    is_active: bool


@router.put("/admin/organizers/{user_id}/approve", response_model=UserOut, tags=["Admin"])
def approve_organizer(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return auth_service.approve_organizer(db, user_id)


@router.post("/admin/staff", response_model=UserOut, status_code=201, tags=["Admin"])
def create_staff(data: StaffCreateRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Create a Staff account assigned to exactly one venue."""
    return auth_service.create_staff(db, data)


@router.put("/admin/users/{user_id}/active", response_model=UserOut, tags=["Admin"])
def set_active(user_id: int, data: ActiveRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Activate / deactivate any account (inactive users cannot log in)."""
    return auth_service.set_user_active(db, user_id, data.is_active)
