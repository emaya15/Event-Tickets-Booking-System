def test_reports_permissions_and_values(api):
    w = api.world()
    c = api.customer()
    api.confirmed_booking(w, c)
    admin, org = w["admin"], w["org"]["token"]

    dash = api.c.get("/reports/admin-dashboard", headers=api.h(admin)).json()
    assert dash["bookings_today"] == 1 and dash["revenue_collected_today"] > 0 and dash["active_events"] == 1
    assert api.c.get("/reports/admin-dashboard", headers=api.h(org)).status_code == 403
    assert api.c.get("/reports/top-events", headers=api.h(org)).status_code == 403
    assert api.c.get("/reports/refunds", headers=api.h(c["token"])).status_code == 403

    sales = api.c.get("/reports/event-sales", headers=api.h(org)).json()
    assert sales["totals"]["tickets_sold"] == 2
    other = api.organizer(admin)
    assert api.c.get("/reports/event-sales", headers=api.h(other["token"]), params={"event_id": w["event_id"]}).status_code == 403
    assert api.c.get("/reports/event-sales", headers=api.h(other["token"])).json()["totals"]["tickets_sold"] == 0

    occ = api.c.get("/reports/occupancy", headers=api.h(admin)).json()
    assert occ["shows"][0]["booked_seats"] == 2
    assert occ["shows"][0]["by_category"]["Silver"]["booked_seats"] == 2

    top = api.c.get("/reports/top-events", headers=api.h(admin)).json()
    assert top["events"][0]["tickets_sold"] == 2


def test_empty_reports_and_validation(api):
    admin = api.admin()
    assert api.c.get("/reports/refunds", headers=api.h(admin)).json()["by_reason"] == []
    assert api.c.get("/reports/top-events", headers=api.h(admin)).json()["events"] == []
    bad = api.c.get("/reports/refunds", headers=api.h(admin), params={"from_date": "2026-02-01", "to_date": "2026-01-01"})
    assert bad.status_code == 400


def test_refund_report_groups_by_reason_and_excel_export(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    api.c.put(f"/bookings/{bid}/cancel", headers=api.h(c["token"]))
    rep = api.c.get("/reports/refunds", headers=api.h(w["admin"])).json()
    assert rep["by_reason"][0]["reason"] == "Customer Cancellation" and rep["total_count"] == 1
    xlsx = api.c.get("/reports/event-sales/export", headers=api.h(w["admin"]))
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"  # xlsx files are zip archives


def test_razorpay_webhook_signature_and_idempotency(api):
    import hashlib, hmac, json

    from app.config import settings

    settings.RAZORPAY_WEBHOOK_SECRET = "whsec_test"
    w = api.world()
    c = api.customer()
    ids = [w["seats"]["A1"]]
    api.lock(c["token"], w["show_id"], ids)
    b = api.book(c["token"], w["show_id"], ids).json()
    from app.database import SessionLocal
    from app.models import Booking

    db = SessionLocal()
    db.get(Booking, b["id"]).razorpay_order_id = "order_123"
    db.commit()
    db.close()
    body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_123"}}}}).encode()
    sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    hdr = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "evt_1", "Content-Type": "application/json"}
    assert api.c.post("/webhooks/razorpay", content=body, headers={**hdr, "X-Razorpay-Signature": "bad"}).status_code == 401
    assert api.c.post("/webhooks/razorpay", content=body, headers=hdr).json()["status"] == "confirmed"
    assert api.c.post("/webhooks/razorpay", content=body, headers=hdr).json()["status"] == "duplicate_ignored"
    assert api.c.get(f"/bookings/{b['id']}", headers=api.h(c["token"])).json()["status"] == "Confirmed"
