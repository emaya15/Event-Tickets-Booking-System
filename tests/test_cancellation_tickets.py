from datetime import datetime, timedelta

from tests.conftest import set_show_start


def _cancel(api, cust, bid):
    return api.c.put(f"/bookings/{bid}/cancel", headers=api.h(cust["token"]))


def test_cancel_more_than_24h_refunds_100_percent_of_tickets(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)  # show is 72h away
    r = _cancel(api, c, bid)
    assert r.status_code == 200
    assert r.json()["refund_amount"] == 300.0  # ticket total only; fee + GST are kept
    assert r.json()["booking"]["status"] == "Cancelled"
    pay = api.c.get(f"/bookings/{bid}/payments", headers=api.h(c["token"])).json()
    assert pay[0]["status"] == "Refunded"
    # seats are free again
    assert api.lock(c["token"], w["show_id"], [w["seats"]["A1"]]).status_code == 200


def test_cancel_between_2_and_24h_refunds_50_percent(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    set_show_start(w["show_id"], datetime.utcnow() + timedelta(hours=10))
    r = _cancel(api, c, bid)
    assert r.status_code == 200 and r.json()["refund_amount"] == 150.0


def test_cancel_less_than_2h_is_not_allowed(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    set_show_start(w["show_id"], datetime.utcnow() + timedelta(hours=1))
    assert _cancel(api, c, bid).status_code == 400


def test_cannot_cancel_other_customers_booking(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    bid = api.confirmed_booking(w, c1)
    assert _cancel(api, c2, bid).status_code == 404


def test_show_cancellation_refunds_everything_and_notifies(api):
    from app.services import email_service

    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    r = api.c.put(f"/shows/{w['show_id']}/cancel", headers=api.h(w["org"]["token"]))
    assert r.status_code == 200 and r.json()["status"] == "Cancelled"
    refunds = api.c.get(f"/bookings/{bid}/refunds", headers=api.h(c["token"])).json()
    assert refunds[0]["reason"] == "Show Cancellation"
    assert refunds[0]["amount"] == 424.8  # full grand total: 300 + 60 fee + 18% GST
    assert any("Show cancelled" in m["subject"] for m in email_service.OUTBOX)
    assert api.lock(c["token"], w["show_id"], [w["seats"]["B1"]]).status_code == 400  # cancelled show


def test_other_organizer_cannot_cancel_show(api):
    w = api.world()
    other = api.organizer(w["admin"])
    assert api.c.put(f"/shows/{w['show_id']}/cancel", headers=api.h(other["token"])).status_code == 403


def test_waitlist_only_for_sold_out_and_queue_notification(api):
    w = api.world(rows=("A",), per_row=2)
    c1, c2 = api.customer(), api.customer()
    assert api.c.post(f"/shows/{w['show_id']}/waitlist", headers=api.h(c2["token"]), json={"seats_requested": 1}).status_code == 400
    bid = api.confirmed_booking(w, c1, seat_names=("A1", "A2"))  # sold out now
    r = api.c.post(f"/shows/{w['show_id']}/waitlist", headers=api.h(c2["token"]), json={"seats_requested": 1})
    assert r.status_code == 201
    assert api.c.post(f"/shows/{w['show_id']}/waitlist", headers=api.h(c2["token"]), json={"seats_requested": 1}).status_code == 409
    assert api.c.post(f"/shows/{w['show_id']}/waitlist", headers=api.h(c1["token"]), json={"seats_requested": 1}).status_code == 409
    from app.services import email_service

    email_service.OUTBOX.clear()
    _cancel(api, c1, bid)
    assert any("Seats available" in m["subject"] for m in email_service.OUTBOX)
    mine = api.c.get(f"/shows/{w['show_id']}/waitlist", headers=api.h(c2["token"])).json()
    assert mine["total"] == 1 and mine["items"][0]["status"] == "Notified"
    assert api.c.delete(f"/waitlist/{r.json()['id']}", headers=api.h(c2["token"])).status_code == 200


# ----------------------------------------------------------------- tickets
def _qr(booking_number):
    from app.services.ticket_service import qr_payload

    return qr_payload(booking_number)


def _booking_number(api, cust, bid):
    return api.c.get(f"/bookings/{bid}", headers=api.h(cust["token"])).json()["booking_number"]


def test_scan_rules(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    number = _booking_number(api, c, bid)
    staff = api.staff(w["admin"], w["venue_id"])
    other_venue = api.c.post("/venues", headers=api.h(w["admin"]), json={"name": "Other", "city": "Pune", "address": "Road 2"}).json()["id"]
    other_staff = api.staff(w["admin"], other_venue)

    # Too early (show is 72h away)
    assert api.c.post("/tickets/scan", headers=api.h(staff["token"]), json={"qr_data": _qr(number)}).status_code == 400
    set_show_start(w["show_id"], datetime.utcnow() + timedelta(minutes=30))

    # customers cannot scan; tampered QR rejected; other venue's staff rejected
    assert api.c.post("/tickets/scan", headers=api.h(c["token"]), json={"qr_data": _qr(number)}).status_code == 403
    bad = api.c.post("/tickets/scan", headers=api.h(staff["token"]), json={"qr_data": number + ".deadbeefdeadbeef"})
    assert bad.status_code == 400
    assert api.c.post("/tickets/scan", headers=api.h(other_staff["token"]), json={"qr_data": _qr(number)}).status_code == 403

    ok = api.c.post("/tickets/scan", headers=api.h(staff["token"]), json={"qr_data": _qr(number)})
    assert ok.status_code == 200
    assert ok.json()["seat_count"] == 2 and ok.json()["seats"] == ["A1", "A2"]
    again = api.c.post("/tickets/scan", headers=api.h(staff["token"]), json={"qr_data": _qr(number)})
    assert again.status_code == 409 and "already used" in again.json()["detail"]

    report = api.c.get(f"/shows/{w['show_id']}/entries", headers=api.h(staff["token"])).json()
    assert report["total_confirmed_bookings"] == 1 and report["entered_bookings"] == 1


def test_cancelled_booking_ticket_is_rejected(api):
    w = api.world()
    c = api.customer()
    bid = api.confirmed_booking(w, c)
    number = _booking_number(api, c, bid)
    _cancel(api, c, bid)
    staff = api.staff(w["admin"], w["venue_id"])
    set_show_start(w["show_id"], datetime.utcnow() + timedelta(minutes=30))
    assert api.c.post("/tickets/scan", headers=api.h(staff["token"]), json={"qr_data": _qr(number)}).status_code == 400


def test_pdf_ticket_only_for_confirmed_and_owner(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    ids = [w["seats"]["B1"]]
    api.lock(c1["token"], w["show_id"], ids)
    pending = api.book(c1["token"], w["show_id"], ids).json()
    assert api.c.get(f"/bookings/{pending['id']}/ticket", headers=api.h(c1["token"])).status_code == 409
    bid = api.confirmed_booking(w, c1)
    assert api.c.get(f"/bookings/{bid}/ticket", headers=api.h(c2["token"])).status_code == 404
