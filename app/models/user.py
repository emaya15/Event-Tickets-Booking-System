from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(10), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Only meaningful for Organizers: they must be approved before creating events.
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Only meaningful for Staff: the single venue they may scan tickets at.
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), nullable=True, index=True)

    venue = relationship("Venue")
    events = relationship("Event", back_populates="organizer")
    bookings = relationship("Booking", back_populates="customer")
