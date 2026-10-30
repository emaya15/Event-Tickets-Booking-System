import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.utils.constants import SeatCategory


class VenueCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    city: str = Field(min_length=2, max_length=100)
    address: str = Field(min_length=3, max_length=300)
    is_active: bool = True


class VenueUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    city: Optional[str] = Field(default=None, min_length=2, max_length=100)
    address: Optional[str] = Field(default=None, min_length=3, max_length=300)
    is_active: Optional[bool] = None


class VenueOut(ORMModel):
    id: int
    name: str
    city: str
    address: str
    is_active: bool


class ScreenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    total_seats: int = Field(gt=0, le=2000)


class ScreenOut(ORMModel):
    id: int
    venue_id: int
    name: str
    total_seats: int


class BulkSeatCreate(BaseModel):
    """Create ``seats_per_row`` seats (numbered from ``start_number``) in each listed row."""

    row_labels: List[str] = Field(min_length=1, max_length=60)
    seats_per_row: int = Field(gt=0, le=100)
    category: SeatCategory
    start_number: int = Field(default=1, ge=1)

    @field_validator("row_labels")
    @classmethod
    def _labels(cls, rows):
        cleaned = []
        for r in rows:
            r = r.strip().upper()
            if not re.fullmatch(r"[A-Z]{1,3}", r):
                raise ValueError("row labels must be 1-3 letters (A, B, ... AA)")
            cleaned.append(r)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("row labels must be unique")
        return cleaned


class SeatStatusUpdate(BaseModel):
    is_active: bool


class SeatOut(ORMModel):
    id: int
    screen_id: int
    row_label: str
    seat_number: int
    category: str
    is_active: bool
