"""String constants used as enum values (stored as VARCHAR for portability)."""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class Role(StrEnum):
    ADMIN = "Admin"
    ORGANIZER = "Organizer"
    CUSTOMER = "Customer"
    STAFF = "Staff"


class SeatCategory(StrEnum):
    SILVER = "Silver"
    GOLD = "Gold"
    RECLINER = "Recliner"


class EventCategory(StrEnum):
    MOVIE = "Movie"
    CONCERT = "Concert"
    COMEDY = "Comedy"
    SPORTS = "Sports"
    WORKSHOP = "Workshop"


class EventStatus(StrEnum):
    DRAFT = "Draft"
    PUBLISHED = "Published"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"


class ShowStatus(StrEnum):
    SCHEDULED = "Scheduled"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"


class SeatState(StrEnum):
    AVAILABLE = "Available"
    LOCKED = "Locked"
    BOOKED = "Booked"
    UNAVAILABLE = "Unavailable"
    YOURS = "Yours"  # only sent over WebSockets / seat map for the viewer's own locks


class BookingStatus(StrEnum):
    PENDING = "Pending Payment"
    CONFIRMED = "Confirmed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class PaymentMethod(StrEnum):
    UPI = "UPI"
    CARD = "Card"
    NET_BANKING = "Net Banking"


class PaymentStatus(StrEnum):
    SUCCESS = "Success"
    FAILED = "Failed"
    REFUNDED = "Refunded"


class SimulateStatus(StrEnum):
    SUCCESS = "Success"
    FAILED = "Failed"


class RefundReason(StrEnum):
    CUSTOMER = "Customer Cancellation"
    SHOW = "Show Cancellation"
    LATE_PAYMENT = "Late Payment"


class RefundStatus(StrEnum):
    PROCESSED = "Processed"


class WaitlistStatus(StrEnum):
    WAITING = "Waiting"
    NOTIFIED = "Notified"


class DiscountType(StrEnum):
    PERCENT = "Percent"
    FLAT = "Flat"
