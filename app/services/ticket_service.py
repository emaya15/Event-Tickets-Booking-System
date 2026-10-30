"""Ticket records, HMAC-signed QR codes, PDF generation and entry scanning."""
import hashlib
import hmac
import io
import logging
import os
import secrets
from datetime import timedelta
from typing import Optional

import qrcode
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BookedSeat, Booking, Event, Screen, Seat, Show, Ticket, User, Venue
from app.utils.constants import BookingStatus, Role, ShowStatus
from app.utils.errors import BadRequest, Conflict, Forbidden, NotFound
from app.utils.time import utc_now

logger = logging.getLogger("app.tickets")


# ----------------------------------------------------------- QR signatures
def sign_booking_number(booking_number: str) -> str:
    return hmac.new(settings.QR_SIGNING_SECRET.encode(), booking_number.encode(), hashlib.sha256).hexdigest()


def qr_payload(booking_number: str, signature: Optional[str] = None) -> str:
    return f"{booking_number}.{signature or sign_booking_number(booking_number)}"


def parse_and_verify_qr(qr_data: str) -> str:
    """Return the booking number if the signature is valid; raise 400 otherwise. Never trust raw QR text."""
    if not qr_data or "." not in qr_data:
        raise BadRequest("Invalid QR code")
    booking_number, _, signature = qr_data.strip().rpartition(".")
    expected = sign_booking_number(booking_number)
    if not hmac.compare_digest(expected, signature):
        raise BadRequest("Invalid QR code signature")
    return booking_number


def create_ticket_record(db: Session, booking: Booking) -> Ticket:
    """Called inside the payment transaction (no commit here)."""
    ticket = Ticket(
        booking_id=booking.id,
        ticket_code="TKT-" + secrets.token_hex(6).upper(),
        qr_signature=sign_booking_number(booking.booking_number),
        is_used=False,
    )
    db.add(ticket)
    return ticket


# ------------------------------------------------------------------- PDF
def _booking_context(db: Session, booking: Booking) -> dict:
    show = db.get(Show, booking.show_id)
    event = db.get(Event, show.event_id)
    screen = db.get(Screen, show.screen_id)
    venue = db.get(Venue, screen.venue_id)
    customer = db.get(User, booking.user_id)
    seats = db.execute(
        select(Seat.row_label, Seat.seat_number, Seat.category)
        .join(BookedSeat, BookedSeat.seat_id == Seat.id)
        .where(BookedSeat.booking_id == booking.id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()
    return {"show": show, "event": event, "screen": screen, "venue": venue, "customer": customer, "seats": seats}


def generate_pdf(db: Session, booking: Booking) -> str:
    """Create (or overwrite) the PDF ticket on disk and return its path."""
    ctx = _booking_context(db, booking)
    os.makedirs(settings.TICKETS_DIR, exist_ok=True)
    path = os.path.join(settings.TICKETS_DIR, f"{booking.booking_number}.pdf")

    qr_img = qrcode.make(qr_payload(booking.booking_number))
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)

    width, height = A5
    pdf = canvas.Canvas(path, pagesize=A5)
    pdf.setTitle(f"Ticket {booking.booking_number}")
    y = height - 20 * mm
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(15 * mm, y, ctx["event"].title[:40])
    pdf.setFont("Helvetica", 10)
    lines = [
        f"Category: {ctx['event'].category}   Language: {ctx['event'].language}",
        f"Venue: {ctx['venue'].name}, {ctx['venue'].city}",
        f"Screen: {ctx['screen'].name}",
        f"Date: {ctx['show'].show_date:%d %b %Y}   Time: {ctx['show'].start_time:%H:%M} UTC",
        f"Booking No: {booking.booking_number}",
        f"Name: {ctx['customer'].name}",
        "Seats: " + ", ".join(f"{r}{n} ({c})" for r, n, c in ctx["seats"]),
        f"Amount paid: Rs. {float(booking.grand_total):,.2f}",
    ]
    y -= 12 * mm
    for line in lines:
        pdf.drawString(15 * mm, y, line[:90])
        y -= 7 * mm
    pdf.drawImage(ImageReader(buf), width - 60 * mm, 15 * mm, 45 * mm, 45 * mm)
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(15 * mm, 12 * mm, "Present the QR code at entry. Valid for one entry only.")
    pdf.showPage()
    pdf.save()

    ticket = db.scalar(select(Ticket).where(Ticket.booking_id == booking.id))
    if ticket is not None:
        ticket.pdf_path = path
        db.commit()
    return path


def get_ticket_pdf(db: Session, user: User, booking_id: int) -> str:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise NotFound("Booking not found")
    if user.role != Role.ADMIN.value and booking.user_id != user.id:
        raise NotFound("Booking not found")  # do not reveal other customers' bookings
    if booking.status != BookingStatus.CONFIRMED.value:
        raise Conflict(f"Tickets are only available for Confirmed bookings (this one is {booking.status})")
    ticket = db.scalar(select(Ticket).where(Ticket.booking_id == booking.id))
    if ticket and ticket.pdf_path and os.path.exists(ticket.pdf_path):
        return ticket.pdf_path
    return generate_pdf(db, booking)


# ------------------------------------------------------------------ scan
def scan_ticket(db: Session, staff: User, qr_data: str) -> dict:
    booking_number = parse_and_verify_qr(qr_data)
    booking = db.scalar(select(Booking).where(Booking.booking_number == booking_number))
    if booking is None:
        raise NotFound("Ticket not found")
    show = db.get(Show, booking.show_id)
    screen = db.get(Screen, show.screen_id)
    if staff.venue_id is None or screen.venue_id != staff.venue_id:
        raise Forbidden("This ticket is for a show at a different venue")
    if booking.status != BookingStatus.CONFIRMED.value:
        raise BadRequest(f"Ticket is not valid: booking is {booking.status}")
    if show.status != ShowStatus.SCHEDULED.value and show.status != ShowStatus.COMPLETED.value:
        raise BadRequest(f"Ticket is not valid: the show is {show.status}")
    now = utc_now()
    if now < show.start_at - timedelta(hours=1):
        raise BadRequest("Entry opens one hour before the show starts")
    if now > show.end_at:
        raise BadRequest("The show has ended; this ticket can no longer be used")
    ticket = db.scalar(select(Ticket).where(Ticket.booking_id == booking.id))
    if ticket is None:
        raise NotFound("Ticket not found")

    # Atomic "claim": only one concurrent scan can flip is_used from 0 to 1.
    result = db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.is_used.is_(False))
        .values(is_used=True, used_at=now, scanned_by=staff.id, updated_at=now)
    )
    db.commit()
    if result.rowcount == 0:
        db.refresh(ticket)
        used_at = ticket.used_at.strftime("%Y-%m-%d %H:%M:%S") if ticket.used_at else "earlier"
        raise Conflict(f"Ticket already used at {used_at} UTC")

    customer = db.get(User, booking.user_id)
    seats = db.execute(
        select(Seat.row_label, Seat.seat_number)
        .join(BookedSeat, BookedSeat.seat_id == Seat.id)
        .where(BookedSeat.booking_id == booking.id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()
    return {
        "message": "Entry allowed",
        "booking_number": booking.booking_number,
        "customer_name": customer.name,
        "seats": [f"{r}{n}" for r, n in seats],
        "seat_count": booking.seat_count,
        "scanned_at": now.isoformat() + "Z",
    }


def entries_report(db: Session, user: User, show_id: int) -> dict:
    show = db.get(Show, show_id)
    if show is None:
        raise NotFound("Show not found")
    screen = db.get(Screen, show.screen_id)
    event = db.get(Event, show.event_id)
    if user.role == Role.STAFF.value:
        if user.venue_id != screen.venue_id:
            raise Forbidden("This show is not at your assigned venue")
    elif user.role == Role.ORGANIZER.value:
        if event.organizer_id != user.id:
            raise Forbidden("You can only view entries for your own events")
    elif user.role != Role.ADMIN.value:
        raise Forbidden("You do not have permission to view entries")

    rows = db.execute(
        select(Booking, Ticket, User)
        .join(Ticket, Ticket.booking_id == Booking.id)
        .join(User, User.id == Booking.user_id)
        .where(Booking.show_id == show_id, Booking.status == BookingStatus.CONFIRMED.value)
        .order_by(Booking.id)
    ).all()
    entered = [
        {
            "booking_number": b.booking_number,
            "customer_name": u.name,
            "seat_count": b.seat_count,
            "entered_at": t.used_at.isoformat() + "Z" if t.used_at else None,
        }
        for b, t, u in rows
        if t.is_used
    ]
    total_confirmed = len(rows)
    return {
        "show_id": show_id,
        "total_confirmed_bookings": total_confirmed,
        "entered_bookings": len(entered),
        "not_yet_entered": total_confirmed - len(entered),
        "total_confirmed_seats": sum(b.seat_count for b, _, _ in rows),
        "entered_seats": sum(e["seat_count"] for e in entered),
        "entries": entered,
    }
