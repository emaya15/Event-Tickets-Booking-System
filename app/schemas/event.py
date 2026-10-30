from datetime import date, datetime, time
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Money, ORMModel
from app.utils.constants import EventCategory, EventStatus, SeatCategory


class EventCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    category: EventCategory
    language: str = Field(min_length=2, max_length=50)
    description: Optional[str] = Field(default=None, max_length=5000)
    duration_minutes: int = Field(ge=30, le=300, description="Between 30 and 300 minutes")
    age_rating: str = Field(min_length=1, max_length=10, description="e.g. U, UA, A, 13+")
    status: EventStatus = EventStatus.DRAFT

    @field_validator("status")
    @classmethod
    def _create_status(cls, v):
        if v not in (EventStatus.DRAFT, EventStatus.PUBLISHED):
            raise ValueError("a new event can only be Draft or Published")
        return v


class EventUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=200)
    category: Optional[EventCategory] = None
    language: Optional[str] = Field(default=None, min_length=2, max_length=50)
    description: Optional[str] = Field(default=None, max_length=5000)
    duration_minutes: Optional[int] = Field(default=None, ge=30, le=300)
    age_rating: Optional[str] = Field(default=None, min_length=1, max_length=10)
    status: Optional[EventStatus] = None


class EventOut(ORMModel):
    id: int
    organizer_id: int
    title: str
    category: str
    language: str
    description: Optional[str] = None
    duration_minutes: int
    age_rating: str
    status: str


class EventListItem(EventOut):
    next_show_at: Optional[datetime] = None
    min_price: Optional[Money] = None


class ShowCreate(BaseModel):
    event_id: int
    screen_id: int
    show_date: date
    start_time: time
    prices: Dict[SeatCategory, Decimal] = Field(
        description="Price per seat category available on the screen, e.g. {\"Silver\": 150, \"Gold\": 250}"
    )

    @field_validator("prices")
    @classmethod
    def _positive(cls, prices):
        if not prices:
            raise ValueError("prices are required")
        for category, price in prices.items():
            if price <= 0:
                raise ValueError(f"price for {category.value} must be greater than zero")
            if price.as_tuple().exponent < -2:
                raise ValueError("prices can have at most 2 decimal places")
        return prices


class ShowUpdate(BaseModel):
    screen_id: Optional[int] = None
    show_date: Optional[date] = None
    start_time: Optional[time] = None
    prices: Optional[Dict[SeatCategory, Decimal]] = None

    @field_validator("prices")
    @classmethod
    def _positive(cls, prices):
        if prices is None:
            return prices
        for category, price in prices.items():
            if price <= 0:
                raise ValueError(f"price for {category.value} must be greater than zero")
            if price.as_tuple().exponent < -2:
                raise ValueError("prices can have at most 2 decimal places")
        return prices


class ShowOut(ORMModel):
    id: int
    event_id: int
    screen_id: int
    show_date: date
    start_time: time
    start_at: datetime
    end_at: datetime
    booking_closes_at: datetime
    status: str
    prices: Dict[str, Money] = {}
    venue_id: Optional[int] = None
    venue_name: Optional[str] = None
    city: Optional[str] = None
    screen_name: Optional[str] = None
    
