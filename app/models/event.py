from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    age_rating: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, default="Draft", nullable=False)

    organizer = relationship("User", back_populates="events")
    shows = relationship("Show", back_populates="event", cascade="all, delete-orphan")


class Show(TimestampMixin, Base):
    __tablename__ = "shows"
    __table_args__ = (Index("ix_shows_screen_start_end", "screen_id", "start_at", "end_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True, nullable=False)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id"), index=True, nullable=False)
    show_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    # Full UTC datetimes derived from show_date + start_time (+ event duration).
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    booking_closes_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, default="Scheduled", nullable=False)

    event = relationship("Event", back_populates="shows")
    screen = relationship("Screen", back_populates="shows")
    prices = relationship("ShowPrice", back_populates="show", cascade="all, delete-orphan")


class ShowPrice(TimestampMixin, Base):
    __tablename__ = "show_prices"
    __table_args__ = (
        UniqueConstraint("show_id", "category", name="uq_show_prices_show_category"),
        CheckConstraint("price > 0", name="price_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    show = relationship("Show", back_populates="prices")
