"""Registration, login, organizer approval, staff creation and admin bootstrap."""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
from app.models import User, Venue
from app.schemas.auth import LoginRequest, RegisterRequest, StaffCreateRequest
from app.utils.constants import Role
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthorized

logger = logging.getLogger("app.auth")


def _email_taken(db: Session, email: str) -> bool:
    return db.scalar(select(User.id).where(User.email == email.lower())) is not None


def register_user(db: Session, data: RegisterRequest) -> User:
    """Public registration: only Customer and Organizer accounts."""
    if data.role not in (Role.CUSTOMER, Role.ORGANIZER):
        raise Forbidden("Only Customer and Organizer accounts can be created through registration")
    if _email_taken(db, data.email):
        raise Conflict("An account with this email already exists")
    user = User(
        name=data.name.strip(),
        email=data.email.lower(),
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=data.role.value,
        is_active=True,
        # Customers need no approval; organizers wait for an Admin.
        is_approved=data.role == Role.CUSTOMER,
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, data: LoginRequest) -> dict:
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Same message for unknown email and wrong password (no account enumeration).
    if user is None or not verify_password(data.password, user.password_hash):
        raise Unauthorized("Incorrect email or password")
    if not user.is_active:
        raise Forbidden("This account has been deactivated")
    token = create_access_token(user.id, user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user,
    }


def approve_organizer(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != Role.ORGANIZER.value:
        raise NotFound("Organizer not found")
    user.is_approved = True
    db.commit()
    return user


def set_user_active(db: Session, user_id: int, is_active: bool) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    user.is_active = is_active
    db.commit()
    return user


def create_staff(db: Session, data: StaffCreateRequest) -> User:
    venue = db.get(Venue, data.venue_id)
    if venue is None:
        raise NotFound("Venue not found")
    if not venue.is_active:
        raise BadRequest("Staff cannot be assigned to an inactive venue")
    if _email_taken(db, data.email):
        raise Conflict("An account with this email already exists")
    user = User(
        name=data.name.strip(),
        email=data.email.lower(),
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=Role.STAFF.value,
        is_active=True,
        is_approved=True,
        venue_id=data.venue_id,
    )
    db.add(user)
    db.commit()
    return user


def create_admin(db: Session, email: str, password: str, name: str, phone: str) -> Optional[User]:
    """Create an Admin account if the email is not already used. Returns None if it exists."""
    if _email_taken(db, email):
        return None
    admin = User(
        name=name,
        email=email.lower(),
        phone=phone,
        password_hash=hash_password(password),
        role=Role.ADMIN.value,
        is_active=True,
        is_approved=True,
    )
    db.add(admin)
    db.commit()
    return admin


def ensure_initial_admin(db: Session) -> None:
    """Startup hook: create the first Admin from ADMIN_EMAIL / ADMIN_PASSWORD (if provided)."""
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        logger.info("ADMIN_EMAIL/ADMIN_PASSWORD not set - skipping initial admin creation")
        return
    created = create_admin(db, settings.ADMIN_EMAIL, settings.ADMIN_PASSWORD, settings.ADMIN_NAME, settings.ADMIN_PHONE)
    if created:
        logger.info("Initial admin account created for %s", settings.ADMIN_EMAIL)
