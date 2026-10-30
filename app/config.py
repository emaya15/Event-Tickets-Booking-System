"""Application settings, loaded from environment variables (and an optional .env file)."""
import os

try:  # python-dotenv is optional at runtime; .env is only a convenience for local dev
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Plain settings object. Values are read once at import time.

    Tests may override attributes directly (e.g. ``settings.LOCK_RATE_LIMIT = 2``).
    """

    def __init__(self) -> None:
        self.APP_ENV = os.getenv("APP_ENV", "development")
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        self.DATABASE_URL = os.getenv(
            "DATABASE_URL", "mysql+pymysql://root:password@localhost:3306/ticket_booking"
        )
        self.REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        self.JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-jwt-secret-change-me")
        self.JWT_ALGORITHM = "HS256"
        self.ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
        self.QR_SIGNING_SECRET = os.getenv("QR_SIGNING_SECRET", "dev-only-insecure-qr-secret-change-me")

        self.ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
        self.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
        self.ADMIN_NAME = os.getenv("ADMIN_NAME", "Platform Admin")
        self.ADMIN_PHONE = os.getenv("ADMIN_PHONE", "9000000000")

        self.SMTP_HOST = os.getenv("SMTP_HOST", "")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USER = os.getenv("SMTP_USER", "")
        self.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
        self.SMTP_FROM = os.getenv("SMTP_FROM", "tickets@example.com")
        self.SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)

        self.RUN_SCHEDULER = _bool("RUN_SCHEDULER", True)

        self.LOCK_RATE_LIMIT = int(os.getenv("LOCK_RATE_LIMIT", "5"))
        self.LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
        self.RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

        self.DYNAMIC_PRICING_ENABLED = _bool("DYNAMIC_PRICING_ENABLED", True)

        self.RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
        self.RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
        self.RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

        # ---- Business constants (kept here so they are easy to find) ----
        self.SEAT_LOCK_SECONDS = 600          # 10 minutes
        self.MAX_SEATS_PER_SHOW = 10
        self.BOOKING_CLOSE_MINUTES = 30
        self.CLEANING_GAP_MINUTES = 15
        self.CONVENIENCE_FEE_PER_TICKET = 30
        self.GST_RATE = "0.18"
        self.EVENT_CACHE_SECONDS = 300
        self.TICKETS_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tickets"
        )


settings = Settings()
