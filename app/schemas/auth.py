import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel
from app.utils.constants import Role

PHONE_RE = re.compile(r"^\d{10}$")


def validate_phone(value: str) -> str:
    if not PHONE_RE.match(value):
        raise ValueError("phone must be exactly 10 digits")
    return value


def validate_password(value: str) -> str:
    if len(value) < 8 or not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("password must be at least 8 characters and contain letters and digits")
    return value


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str
    password: str = Field(max_length=72)  # bcrypt only uses the first 72 bytes
    role: Role = Role.CUSTOMER

    _phone = field_validator("phone")(validate_phone)
    _password = field_validator("password")(validate_password)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class StaffCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str
    password: str = Field(max_length=72)
    venue_id: int

    _phone = field_validator("phone")(validate_phone)
    _password = field_validator("password")(validate_password)


class UserOut(ORMModel):
    id: int
    name: str
    email: str
    phone: str
    role: str
    is_active: bool
    is_approved: bool
    venue_id: Optional[int] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
