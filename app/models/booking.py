from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), index=True, nullable=False)
    seat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    convenience_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    coupon_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(String(60), unique=True, nullable=True)

    customer = relationship("User", back_populates="bookings")
    show = relationship("Show")
    seats = relationship("BookedSeat", back_populates="booking", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="booking", cascade="all, delete-orphan")
    refunds = relationship("Refund", back_populates="booking", cascade="all, delete-orphan")
    ticket = relationship("Ticket", back_populates="booking", uselist=False, cascade="all, delete-orphan")


class BookedSeat(TimestampMixin, Base):
    __tablename__ = "booked_seats"
    __table_args__ = (
        UniqueConstraint("booking_id", "seat_id", name="uq_booked_seats_booking_seat"),
        # active_key is 1 only while the booking is Confirmed and NULL otherwise.
        # NULLs never collide in a unique index, so this guarantees a seat can be
        # confirmed at most once per show, while cancelled/expired history is kept.
        UniqueConstraint("show_id", "seat_id", "active_key", name="uq_booked_seats_show_seat_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True, nullable=False)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), index=True, nullable=False)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), index=True, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    active_key: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    booking = relationship("Booking", back_populates="seats")
    seat = relationship("Seat")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        # success_key is 1 for Success/Refunded payments, NULL for Failed ones:
        # a booking can therefore be successfully charged only once.
        UniqueConstraint("booking_id", "success_key", name="uq_payments_booking_success"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True, nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gateway: Mapped[str] = mapped_column(String(20), default="Mock", nullable=False)
    gateway_payment_id: Mapped[Optional[str]] = mapped_column(String(60), unique=True, nullable=True)
    success_key: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    booking = relationship("Booking", back_populates="payments")
    refunds = relationship("Refund", back_populates="payment")


class Refund(TimestampMixin, Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True, nullable=False)
    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id"), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    refunded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    booking = relationship("Booking", back_populates="refunds")
    payment = relationship("Payment", back_populates="refunds")


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, nullable=False)
    ticket_code: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    qr_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scanned_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    booking = relationship("Booking", back_populates="ticket")


class Waitlist(TimestampMixin, Base):
    __tablename__ = "waitlist"
    __table_args__ = (UniqueConstraint("show_id", "user_id", name="uq_waitlist_show_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    seats_requested: Mapped[int] = mapped_column(Integer, nullable=False)
    preferred_category: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Waiting", nullable=False)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User")
    show = relationship("Show")


class Coupon(TimestampMixin, Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    discount_type: Mapped[str] = mapped_column(String(10), nullable=False)  # Percent | Flat
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_tickets: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WebhookEvent(TimestampMixin, Base):
    """Remembers processed gateway webhooks so each one is handled exactly once."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
