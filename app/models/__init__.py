"""Import every model so Alembic and SQLAlchemy see the full metadata."""
from app.models.booking import (  # noqa: F401
    BookedSeat,
    Booking,
    Coupon,
    Payment,
    Refund,
    Ticket,
    Waitlist,
    WebhookEvent,
)
from app.models.event import Event, Show, ShowPrice  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.venue import Screen, Seat, Venue  # noqa: F401
