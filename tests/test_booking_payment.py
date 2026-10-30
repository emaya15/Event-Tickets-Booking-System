from datetime import timedelta
from decimal import Decimal

from app.database import SessionLocal
from app.models import Booking
from app.redis_client import get_redis
from app.services import booking_service
from app.services.seat_service import lock_key
from app.utils.time import utc_now


def test_bill_calculation_rules():
    bill = booking_service.calculate_bill([Decimal("150"), Decimal("250")])
    assert bill["ticket_total"] == Decimal("400.00")
    assert bill["convenience_fee"] == Decimal("60.00")
    assert bill["gst_amount"] == Decimal("82.80")  # 18% of (400 + 60)
    assert bill["grand_total"] == Decimal("542.80")


def test_booking_requires_own_locks(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    ids = [w["seats"]["A1"], w["seats"]["A2"]]
    assert api.book(c1["token"], w["show_id"], ids).status_code == 409  # nothing locked
    api.lock(c1["token"], w["show_id"], ids)
    assert api.book(c2["token"], w["show_id"], ids).status_code == 409  # someone else's locks
    b = api.book(c1["token"], w["show_id"], ids)
    assert b.status_code == 201
    data = b.json()
    assert data["status"] == "Pending Payment"
    assert data["booking_number"].startswith("BKG-") and data["booking_number"].endswith("-0001")
    assert data["ticket_total"] == 300.0 and data["convenience_fee"] == 60.0


def test_successful_payment_confirms_booking_and_creates_ticket(api):
    w = api.world()
    c = api.customer()
    ids = [w["seats"]["A1"], w["seats"]["A2"]]
    api.lock(c["token"], w["show_id"], ids)
    b = api.book(c["token"], w["show_id"], ids).json()
    r = api.pay(c["token"], b["id"])
    assert r.status_code == 200
    assert r.json()["payment"]["status"] == "Success"
    assert r.json()["payment"]["amount"] == b["grand_total"]  # server-derived
    assert r.json()["booking"]["status"] == "Confirmed"
    assert get_redis().get(lock_key(w["show_id"], ids[0])) is None  # lock released after commit
    seat_map = api.c.get(f"/shows/{w['show_id']}/seats", headers=api.h(c["token"])).json()
    assert {s["seat_id"]: s["status"] for s in seat_map["seats"]}[ids[0]] == "Booked"
    pdf = api.c.get(f"/bookings/{b['id']}/ticket", headers=api.h(c["token"]))
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    from app.services import email_service

    assert any("Booking confirmed" in m["subject"] for m in email_service.OUTBOX)


def test_failed_payment_allows_retry_and_duplicate_payment_rejected(api):
    w = api.world()
    c = api.customer()
    ids = [w["seats"]["A1"]]
    api.lock(c["token"], w["show_id"], ids)
    b = api.book(c["token"], w["show_id"], ids).json()
    failed = api.pay(c["token"], b["id"], simulate="Failed")
    assert failed.status_code == 200 and failed.json()["payment"]["status"] == "Failed"
    assert api.pay(c["token"], b["id"]).status_code == 200  # retry succeeds
    assert api.pay(c["token"], b["id"]).status_code == 409  # cannot pay twice
    payments = api.c.get(f"/bookings/{b['id']}/payments", headers=api.h(c["token"])).json()
    assert [p["status"] for p in payments] == ["Failed", "Success"]


def test_payment_client_cannot_override_amount(api):
    w = api.world()
    c = api.customer()
    api.lock(c["token"], w["show_id"], [w["seats"]["A1"]])
    b = api.book(c["token"], w["show_id"], [w["seats"]["A1"]]).json()
    r = api.c.post(
        f"/bookings/{b['id']}/pay", headers=api.h(c["token"]),
        json={"payment_method": "UPI", "simulate_status": "Success", "amount": 1},
    )
    assert r.status_code == 200 and r.json()["payment"]["amount"] == b["grand_total"]


def test_late_payment_seats_taken_triggers_full_refund(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    sid = w["seats"]["A1"]
    api.lock(c1["token"], w["show_id"], [sid])
    b1 = api.book(c1["token"], w["show_id"], [sid]).json()
    # c1's hold expires; c2 grabs and buys the seat
    get_redis().delete(lock_key(w["show_id"], sid))
    db = SessionLocal()
    db.get(Booking, b1["id"]).expires_at = utc_now() - timedelta(seconds=5)  # hold has lapsed (job not run yet)
    db.commit()
    db.close()
    api.lock(c2["token"], w["show_id"], [sid])
    b2 = api.book(c2["token"], w["show_id"], [sid]).json()
    assert api.pay(c2["token"], b2["id"]).status_code == 200
    # c1 pays late
    late = api.pay(c1["token"], b1["id"])
    assert late.status_code == 409
    body = late.json()
    assert body["payment"]["status"] == "Refunded"
    assert body["refund"]["reason"] == "Late Payment" and body["refund"]["amount"] == b1["grand_total"]
    assert body["booking"]["status"] == "Cancelled"
    refunds = api.c.get(f"/bookings/{b1['id']}/refunds", headers=api.h(c1["token"])).json()
    assert len(refunds) == 1


def test_expired_booking_cannot_be_paid_and_job_expires_it(api):
    from app.jobs.tasks import expire_pending_bookings

    w = api.world()
    c = api.customer()
    api.lock(c["token"], w["show_id"], [w["seats"]["A1"]])
    b = api.book(c["token"], w["show_id"], [w["seats"]["A1"]]).json()
    db = SessionLocal()
    bk = db.get(Booking, b["id"])
    bk.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    db.close()
    assert expire_pending_bookings() == 1
    assert api.pay(c["token"], b["id"]).status_code == 409
    assert api.c.get(f"/bookings/{b['id']}", headers=api.h(c["token"])).json()["status"] == "Expired"


def test_customers_see_only_own_bookings(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    bid = api.confirmed_booking(w, c1)
    assert api.c.get(f"/bookings/{bid}", headers=api.h(c2["token"])).status_code == 404
    assert api.c.get("/bookings", headers=api.h(c2["token"])).json()["total"] == 0
    assert api.c.get("/bookings", headers=api.h(c1["token"]), params={"status": "Confirmed"}).json()["total"] == 1
    assert api.c.get("/bookings", headers=api.h(w["org"]["token"])).json()["total"] == 1  # organizer of the event
    assert api.c.get("/bookings", headers=api.h(w["admin"])).json()["total"] == 1


def test_coupon_discount_applied_server_side(api):
    w = api.world()
    c = api.customer()
    r = api.c.post(
        "/coupons", headers=api.h(w["admin"]),
        json={"code": "SAVE10", "discount_type": "Percent", "discount_value": 10, "min_tickets": 2},
    )
    assert r.status_code == 201
    ids = [w["seats"]["A1"], w["seats"]["A2"]]
    api.lock(c["token"], w["show_id"], ids)
    assert api.book(c["token"], w["show_id"], [ids[0]], coupon="SAVE10").status_code in (400, 409)
    b = api.book(c["token"], w["show_id"], ids, coupon="save10")
    assert b.status_code == 201
    assert b.json()["discount_amount"] == 30.0
    # (300 - 30 + 60) = 330 ; GST 59.40 ; total 389.40
    assert b.json()["grand_total"] == 389.4


def test_dynamic_pricing_surge_above_80_percent(api):
    w = api.world(rows=("A",), per_row=6)
    cust = [api.customer() for _ in range(3)]
    ids_a = [w["seats"][f"A{i}"] for i in (1, 2, 3)]
    ids_b = [w["seats"][f"A{i}"] for i in (4, 5)]
    # book 5 of 6 seats (83% > 80%)
    for c, ids in ((cust[0], ids_a), (cust[1], ids_b)):
        assert api.lock(c["token"], w["show_id"], ids).status_code == 200
        bid = api.book(c["token"], w["show_id"], ids).json()["id"]
        assert api.pay(c["token"], bid).status_code == 200
    seat_map = api.c.get(f"/shows/{w['show_id']}/seats", headers=api.h(cust[2]["token"])).json()
    last = next(s for s in seat_map["seats"] if s["seat_id"] == w["seats"]["A6"])
    assert last["price"] == 165.0  # 150 + 10%
