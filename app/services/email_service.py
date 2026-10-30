"""Email notifications over SMTP (smtplib). Falls back to MOCK logging without SMTP credentials.

Email problems are logged and NEVER propagate: a failed email must not undo a booking/payment.
"""
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from fastapi import BackgroundTasks

from app.config import settings

logger = logging.getLogger("app.email")

# Tests (and curious developers) can inspect what would have been sent.
OUTBOX: list = []


def send_email(to: str, subject: str, body: str, attachment_path: Optional[str] = None) -> bool:
    """Send one email. Returns True if sent (or mock-logged), False if it failed."""
    try:
        if not settings.SMTP_HOST:
            logger.info("[MOCK EMAIL] to=%s subject=%r attachment=%s\n%s", to, subject, attachment_path, body)
            OUTBOX.append({"to": to, "subject": subject, "body": body, "attachment": attachment_path})
            del OUTBOX[:-200]  # keep memory bounded
            return True
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.SMTP_FROM, to, subject
        msg.set_content(body)
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as fh:
                msg.add_attachment(
                    fh.read(), maintype="application", subtype="pdf", filename=os.path.basename(attachment_path)
                )
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s (subject=%r)", to, subject)
        return False


def dispatch(background: Optional[BackgroundTasks], to: str, subject: str, body: str, attachment_path: Optional[str] = None):
    """Queue the email on FastAPI BackgroundTasks (or send inline when called from a scheduled job)."""
    if background is not None:
        background.add_task(send_email, to, subject, body, attachment_path)
    else:
        send_email(to, subject, body, attachment_path)


def money(value) -> str:
    return f"Rs. {float(value):,.2f}"


# --------------------------------------------------------------- templates
def booking_confirmed(background, user, booking, event_title, pdf_path):
    dispatch(
        background,
        user.email,
        f"Booking confirmed - {booking.booking_number}",
        f"Hi {user.name},\n\nYour booking {booking.booking_number} for {event_title} is confirmed.\n"
        f"Seats: {booking.seat_count} | Amount paid: {money(booking.grand_total)}\n"
        "Your PDF ticket is attached. Show the QR code at the entrance.\n",
        pdf_path,
    )


def payment_failed(background, user, booking):
    dispatch(
        background,
        user.email,
        f"Payment failed - {booking.booking_number}",
        f"Hi {user.name},\n\nYour payment for booking {booking.booking_number} did not go through. "
        "You can retry until the booking expires.\n",
    )


def booking_cancelled(background, user, booking, refund_amount):
    dispatch(
        background,
        user.email,
        f"Booking cancelled - {booking.booking_number}",
        f"Hi {user.name},\n\nYour booking {booking.booking_number} has been cancelled.\n"
        f"Refund amount: {money(refund_amount)} (mock refund).\n",
    )


def late_payment_refund(background, user, booking, refund_amount):
    dispatch(
        background,
        user.email,
        f"Seats no longer available - refund issued for {booking.booking_number}",
        f"Hi {user.name},\n\nYour payment arrived after your seat hold expired and the seats were taken. "
        f"We have refunded {money(refund_amount)} in full. The booking is cancelled.\n",
    )


def show_cancelled(background, user, booking, event_title, refund_amount):
    dispatch(
        background,
        user.email,
        f"Show cancelled - {event_title}",
        f"Hi {user.name},\n\nUnfortunately the show for booking {booking.booking_number} ({event_title}) "
        f"has been cancelled by the organizer. A full refund of {money(refund_amount)} has been issued.\n",
    )


def waitlist_available(background, user, event_title, show_id):
    dispatch(
        background,
        user.email,
        f"Seats available - {event_title}",
        f"Hi {user.name},\n\nSeats you waitlisted for ({event_title}, show #{show_id}) are available now. "
        "Book quickly - they are first come, first served.\n",
    )


def show_reminder(background, user, booking, event_title, start_at):
    dispatch(
        background,
        user.email,
        f"Reminder: {event_title} starts soon",
        f"Hi {user.name},\n\nYour show {event_title} starts at {start_at:%Y-%m-%d %H:%M} UTC "
        f"(booking {booking.booking_number}). Bring your ticket QR code.\n",
    )
