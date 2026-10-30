"""Test setup: SQLite (temp file) + fakeredis, so no MySQL/Redis server is needed to run pytest.

The application itself never calls create_all(); only these tests build the schema directly
(production schema creation is done exclusively by Alembic migrations).
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ticket_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["RUN_SCHEDULER"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["ADMIN_EMAIL"] = "admin@test.com"
os.environ["ADMIN_PASSWORD"] = "AdminPass123"
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["QR_SIGNING_SECRET"] = "test-qr-secret"
os.environ["LOGIN_RATE_LIMIT"] = "1000"
os.environ["LOCK_RATE_LIMIT"] = "1000"

from datetime import datetime, timedelta  # noqa: E402

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import redis_client  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import Show  # noqa: E402
from app.main import app  # noqa: E402

settings.TICKETS_DIR = os.path.join(_tmp, "tickets")


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh schema + fresh fake Redis for every test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    redis_client.set_redis(fakeredis.FakeRedis(decode_responses=True))
    settings.LOCK_RATE_LIMIT = 1000
    settings.LOGIN_RATE_LIMIT = 1000
    from app.services import email_service

    email_service.OUTBOX.clear()
    yield


@pytest.fixture()
def client(clean_state):
    with TestClient(app) as c:
        yield c


class Api:
    """Tiny helper that wraps the test client with the calls every test needs."""

    def __init__(self, client: TestClient):
        self.c = client
        self._n = 0

    def _email(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}@test.com"

    @staticmethod
    def h(token):
        return {"Authorization": f"Bearer {token}"}

    def login(self, email, password):
        r = self.c.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def admin(self):
        return self.login("admin@test.com", "AdminPass123")

    def register(self, role="Customer", prefix=None):
        email = self._email(prefix or role.lower())
        r = self.c.post(
            "/auth/register",
            json={"name": f"{role} User", "email": email, "phone": "9876543210", "password": "Passw0rdX", "role": role},
        )
        assert r.status_code == 201, r.text
        return {"id": r.json()["id"], "email": email, "token": self.login(email, "Passw0rdX")}

    def customer(self):
        return self.register("Customer")

    def organizer(self, admin_token):
        org = self.register("Organizer")
        r = self.c.put(f"/admin/organizers/{org['id']}/approve", headers=self.h(admin_token))
        assert r.status_code == 200, r.text
        return org

    def staff(self, admin_token, venue_id):
        email = self._email("staff")
        r = self.c.post(
            "/admin/staff",
            headers=self.h(admin_token),
            json={"name": "Gate Staff", "email": email, "phone": "9876543211", "password": "Passw0rdX", "venue_id": venue_id},
        )
        assert r.status_code == 201, r.text
        return {"id": r.json()["id"], "email": email, "token": self.login(email, "Passw0rdX")}

    def world(self, rows=("A", "B"), per_row=6, hours_ahead=72):
        """Venue + screen (rows A=Silver, B=Gold ...) + organizer + published event + one show."""
        admin = self.admin()
        v = self.c.post("/venues", headers=self.h(admin), json={"name": "PVR", "city": "Hyderabad", "address": "Road 1"})
        assert v.status_code == 201, v.text
        venue_id = v.json()["id"]
        s = self.c.post(f"/venues/{venue_id}/screens", headers=self.h(admin), json={"name": "Screen 1", "total_seats": 100})
        screen_id = s.json()["id"]
        categories = ["Silver", "Gold", "Recliner"]
        for i, row in enumerate(rows):
            r = self.c.post(
                f"/screens/{screen_id}/seats/bulk",
                headers=self.h(admin),
                json={"row_labels": [row], "seats_per_row": per_row, "category": categories[i % 3]},
            )
            assert r.status_code == 201, r.text
        org = self.organizer(admin)
        e = self.c.post(
            "/events",
            headers=self.h(org["token"]),
            json={
                "title": "Space Movie", "category": "Movie", "language": "Telugu", "description": "x",
                "duration_minutes": 120, "age_rating": "UA", "status": "Published",
            },
        )
        assert e.status_code == 201, e.text
        event_id = e.json()["id"]
        show = self.make_show(org["token"], event_id, screen_id, hours_ahead, len(rows))
        layout = self.c.get(f"/screens/{screen_id}/layout", headers=self.h(admin)).json()
        seat_ids = {f"{row['row_label']}{seat['seat_number']}": seat["id"] for row in layout["rows"] for seat in row["seats"]}
        return {
            "admin": admin, "org": org, "venue_id": venue_id, "screen_id": screen_id,
            "event_id": event_id, "show_id": show["id"], "seats": seat_ids,
        }

    def make_show(self, org_token, event_id, screen_id, hours_ahead=72, categories=2):
        start = datetime.utcnow() + timedelta(hours=hours_ahead)
        prices = {"Silver": 150, "Gold": 250, "Recliner": 400}
        wanted = dict(list(prices.items())[:categories])
        r = self.c.post(
            f"/events/{event_id}/shows",
            headers=self.h(org_token),
            json={"screen_id": screen_id, "show_date": start.date().isoformat(), "start_time": start.strftime("%H:%M:%S"), "prices": wanted},
        )
        assert r.status_code == 201, r.text
        return r.json()

    def lock(self, token, show_id, seat_ids):
        return self.c.post(f"/shows/{show_id}/lock-seats", headers=self.h(token), json={"seat_ids": seat_ids})

    def book(self, token, show_id, seat_ids, coupon=None):
        body = {"show_id": show_id, "seat_ids": seat_ids}
        if coupon:
            body["coupon_code"] = coupon
        return self.c.post("/bookings", headers=self.h(token), json=body)

    def pay(self, token, booking_id, simulate="Success", method="UPI"):
        return self.c.post(
            f"/bookings/{booking_id}/pay", headers=self.h(token), json={"payment_method": method, "simulate_status": simulate}
        )

    def confirmed_booking(self, w, cust, seat_names=("A1", "A2")):
        ids = [w["seats"][n] for n in seat_names]
        assert self.lock(cust["token"], w["show_id"], ids).status_code == 200
        b = self.book(cust["token"], w["show_id"], ids)
        assert b.status_code == 201, b.text
        p = self.pay(cust["token"], b.json()["id"])
        assert p.status_code == 200, p.text
        return b.json()["id"]


def set_show_start(show_id: int, start_at: datetime, minutes: int = 120):
    """Move a show in time directly in the DB (to test time-dependent rules)."""
    db = SessionLocal()
    try:
        show = db.get(Show, show_id)
        show.start_at = start_at
        show.show_date = start_at.date()
        show.start_time = start_at.time()
        show.end_at = start_at + timedelta(minutes=minutes)
        show.booking_closes_at = start_at - timedelta(minutes=30)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def api(client):
    return Api(client)
