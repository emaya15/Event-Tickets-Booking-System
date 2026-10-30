"""Initial schema: every table, relationship, index and constraint.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _ts():
    """created_at / updated_at columns required on every table."""
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _id():
    return sa.Column("id", sa.Integer(), autoincrement=True, nullable=False)


def upgrade() -> None:
    # ---------------------------------------------------------------- venues
    op.create_table(
        "venues",
        _id(),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_venues"),
    )
    op.create_index("ix_venues_city", "venues", ["city"])

    # ----------------------------------------------------------------- users
    op.create_table(
        "users",
        _id(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(10), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], name="fk_users_venue_id_venues"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_venue_id", "users", ["venue_id"])

    # --------------------------------------------------------------- screens
    op.create_table(
        "screens",
        _id(),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("total_seats", sa.Integer(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_screens"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], name="fk_screens_venue_id_venues"),
        sa.UniqueConstraint("venue_id", "name", name="uq_screens_venue_name"),
    )
    op.create_index("ix_screens_venue_id", "screens", ["venue_id"])

    # ----------------------------------------------------------------- seats
    op.create_table(
        "seats",
        _id(),
        sa.Column("screen_id", sa.Integer(), nullable=False),
        sa.Column("row_label", sa.String(5), nullable=False),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_seats"),
        sa.ForeignKeyConstraint(["screen_id"], ["screens.id"], name="fk_seats_screen_id_screens"),
        sa.UniqueConstraint("screen_id", "row_label", "seat_number", name="uq_seats_screen_row_number"),
    )
    op.create_index("ix_seats_screen_id", "seats", ["screen_id"])

    # ---------------------------------------------------------------- events
    op.create_table(
        "events",
        _id(),
        sa.Column("organizer_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("age_rating", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(["organizer_id"], ["users.id"], name="fk_events_organizer_id_users"),
    )
    op.create_index("ix_events_organizer_id", "events", ["organizer_id"])
    op.create_index("ix_events_title", "events", ["title"])
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_language", "events", ["language"])
    op.create_index("ix_events_status", "events", ["status"])

    # ----------------------------------------------------------------- shows
    op.create_table(
        "shows",
        _id(),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("screen_id", sa.Integer(), nullable=False),
        sa.Column("show_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("booking_closes_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_shows"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_shows_event_id_events"),
        sa.ForeignKeyConstraint(["screen_id"], ["screens.id"], name="fk_shows_screen_id_screens"),
    )
    op.create_index("ix_shows_event_id", "shows", ["event_id"])
    op.create_index("ix_shows_screen_id", "shows", ["screen_id"])
    op.create_index("ix_shows_show_date", "shows", ["show_date"])
    op.create_index("ix_shows_start_at", "shows", ["start_at"])
    op.create_index("ix_shows_end_at", "shows", ["end_at"])
    op.create_index("ix_shows_status", "shows", ["status"])
    op.create_index("ix_shows_screen_start_end", "shows", ["screen_id", "start_at", "end_at"])

    # ----------------------------------------------------------- show_prices
    op.create_table(
        "show_prices",
        _id(),
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_show_prices"),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], name="fk_show_prices_show_id_shows"),
        sa.UniqueConstraint("show_id", "category", name="uq_show_prices_show_category"),
        sa.CheckConstraint("price > 0", name=op.f("ck_show_prices_price_positive")),
    )
    op.create_index("ix_show_prices_show_id", "show_prices", ["show_id"])

    # -------------------------------------------------------------- bookings
    op.create_table(
        "bookings",
        _id(),
        sa.Column("booking_number", sa.String(30), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("seat_count", sa.Integer(), nullable=False),
        sa.Column("ticket_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("convenience_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("gst_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("coupon_code", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
        sa.Column("razorpay_order_id", sa.String(60), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_bookings"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_bookings_user_id_users"),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], name="fk_bookings_show_id_shows"),
        sa.UniqueConstraint("razorpay_order_id", name="uq_bookings_razorpay_order_id"),
    )
    op.create_index("ix_bookings_booking_number", "bookings", ["booking_number"], unique=True)
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])
    op.create_index("ix_bookings_show_id", "bookings", ["show_id"])
    op.create_index("ix_bookings_status", "bookings", ["status"])
    op.create_index("ix_bookings_expires_at", "bookings", ["expires_at"])

    # ---------------------------------------------------------- booked_seats
    op.create_table(
        "booked_seats",
        _id(),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("seat_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("active_key", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_booked_seats"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_booked_seats_booking_id_bookings"),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], name="fk_booked_seats_show_id_shows"),
        sa.ForeignKeyConstraint(["seat_id"], ["seats.id"], name="fk_booked_seats_seat_id_seats"),
        sa.UniqueConstraint("booking_id", "seat_id", name="uq_booked_seats_booking_seat"),
        sa.UniqueConstraint("show_id", "seat_id", "active_key", name="uq_booked_seats_show_seat_active"),
    )
    op.create_index("ix_booked_seats_booking_id", "booked_seats", ["booking_id"])
    op.create_index("ix_booked_seats_show_id", "booked_seats", ["show_id"])
    op.create_index("ix_booked_seats_seat_id", "booked_seats", ["seat_id"])

    # -------------------------------------------------------------- payments
    op.create_table(
        "payments",
        _id(),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.String(40), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("gateway", sa.String(20), nullable=False),
        sa.Column("gateway_payment_id", sa.String(60), nullable=True),
        sa.Column("success_key", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_payments_booking_id_bookings"),
        sa.UniqueConstraint("gateway_payment_id", name="uq_payments_gateway_payment_id"),
        sa.UniqueConstraint("booking_id", "success_key", name="uq_payments_booking_success"),
    )
    op.create_index("ix_payments_booking_id", "payments", ["booking_id"])
    op.create_index("ix_payments_transaction_id", "payments", ["transaction_id"], unique=True)
    op.create_index("ix_payments_status", "payments", ["status"])

    # --------------------------------------------------------------- refunds
    op.create_table(
        "refunds",
        _id(),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("refunded_at", sa.DateTime(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_refunds"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_refunds_booking_id_bookings"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name="fk_refunds_payment_id_payments"),
    )
    op.create_index("ix_refunds_booking_id", "refunds", ["booking_id"])
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])
    op.create_index("ix_refunds_reason", "refunds", ["reason"])

    # --------------------------------------------------------------- tickets
    op.create_table(
        "tickets",
        _id(),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("ticket_code", sa.String(30), nullable=False),
        sa.Column("qr_signature", sa.String(64), nullable=False),
        sa.Column("pdf_path", sa.String(300), nullable=True),
        sa.Column("is_used", sa.Boolean(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("scanned_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_tickets"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_tickets_booking_id_bookings"),
        sa.ForeignKeyConstraint(["scanned_by"], ["users.id"], name="fk_tickets_scanned_by_users"),
        sa.UniqueConstraint("booking_id", name="uq_tickets_booking_id"),
    )
    op.create_index("ix_tickets_ticket_code", "tickets", ["ticket_code"], unique=True)

    # -------------------------------------------------------------- waitlist
    op.create_table(
        "waitlist",
        _id(),
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("seats_requested", sa.Integer(), nullable=False),
        sa.Column("preferred_category", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_waitlist"),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], name="fk_waitlist_show_id_shows"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_waitlist_user_id_users"),
        sa.UniqueConstraint("show_id", "user_id", name="uq_waitlist_show_user"),
    )
    op.create_index("ix_waitlist_show_id", "waitlist", ["show_id"])
    op.create_index("ix_waitlist_user_id", "waitlist", ["user_id"])

    # --------------------------------------------------------------- coupons
    op.create_table(
        "coupons",
        _id(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("discount_type", sa.String(10), nullable=False),
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("min_tickets", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_coupons"),
    )
    op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)

    # -------------------------------------------------------- webhook_events
    op.create_table(
        "webhook_events",
        _id(),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_events"),
        sa.UniqueConstraint("event_id", name="uq_webhook_events_event_id"),
    )


def downgrade() -> None:
    for table in (
        "webhook_events",
        "coupons",
        "waitlist",
        "tickets",
        "refunds",
        "payments",
        "booked_seats",
        "bookings",
        "show_prices",
        "shows",
        "events",
        "seats",
        "screens",
        "users",
        "venues",
    ):
        op.drop_table(table)
