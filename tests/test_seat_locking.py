import threading

from app.redis_client import get_redis
from app.services.seat_service import lock_key


def test_lock_and_seat_map_states(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    ids = [w["seats"]["A1"], w["seats"]["A2"]]
    r = api.lock(c1["token"], w["show_id"], ids)
    assert r.status_code == 200
    assert 0 < r.json()["expires_in_seconds"] <= 600
    assert get_redis().ttl(lock_key(w["show_id"], ids[0])) > 0

    seat_map = api.c.get(f"/shows/{w['show_id']}/seats", headers=api.h(c2["token"])).json()
    by_id = {s["seat_id"]: s for s in seat_map["seats"]}
    assert by_id[ids[0]]["status"] == "Locked" and by_id[ids[0]]["locked_by_you"] is False
    assert by_id[ids[0]]["price"] == 150.0
    mine = api.c.get(f"/shows/{w['show_id']}/seats", headers=api.h(c1["token"])).json()
    assert {s["seat_id"]: s for s in mine["seats"]}[ids[0]]["locked_by_you"] is True


def test_lock_is_all_or_nothing_409(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    assert api.lock(c1["token"], w["show_id"], [w["seats"]["A1"]]).status_code == 200
    r = api.lock(c2["token"], w["show_id"], [w["seats"]["B1"], w["seats"]["A1"]])
    assert r.status_code == 409
    assert r.json()["unavailable_seats"][0]["seat_id"] == w["seats"]["A1"]
    # B1 must NOT remain locked after the failed request
    assert get_redis().get(lock_key(w["show_id"], w["seats"]["B1"])) is None


def test_lock_expires_automatically(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    sid = w["seats"]["A1"]
    api.lock(c1["token"], w["show_id"], [sid])
    get_redis().delete(lock_key(w["show_id"], sid))  # what Redis does when the 10-minute TTL elapses
    assert api.lock(c2["token"], w["show_id"], [sid]).status_code == 200


def test_max_ten_seats_per_show(api):
    w = api.world(rows=("A", "B", "C"), per_row=8)
    c = api.customer()
    first = [w["seats"][f"A{i}"] for i in range(1, 9)] + [w["seats"]["B1"], w["seats"]["B2"]]
    assert api.lock(c["token"], w["show_id"], first).status_code == 200
    assert api.lock(c["token"], w["show_id"], [w["seats"]["C1"]]).status_code == 400


def test_single_empty_seat_rule(api):
    w = api.world(rows=("A",), per_row=6)
    c1, c2 = api.customer(), api.customer()
    assert api.lock(c1["token"], w["show_id"], [w["seats"]["A1"], w["seats"]["A2"]]).status_code == 200
    # Taking A4 would strand A3 between A2 and A4
    r = api.lock(c2["token"], w["show_id"], [w["seats"]["A4"]])
    assert r.status_code == 409
    assert "A3" in r.json()["stranded_seats"]
    # A3 itself, or A3+A4, is fine
    assert api.lock(c2["token"], w["show_id"], [w["seats"]["A3"], w["seats"]["A4"]]).status_code == 200


def test_cannot_release_someone_elses_lock(api):
    w = api.world()
    c1, c2 = api.customer(), api.customer()
    sid = w["seats"]["A1"]
    api.lock(c1["token"], w["show_id"], [sid])
    r = api.c.request("DELETE", f"/shows/{w['show_id']}/lock-seats", headers=api.h(c2["token"]), json={"seat_ids": [sid]})
    assert r.status_code == 409
    ok = api.c.request("DELETE", f"/shows/{w['show_id']}/lock-seats", headers=api.h(c1["token"]), json={"seat_ids": [sid]})
    assert ok.status_code == 200
    assert get_redis().get(lock_key(w["show_id"], sid)) is None


def test_lock_rate_limit(api):
    from app.config import settings

    w = api.world()
    c = api.customer()
    settings.LOCK_RATE_LIMIT = 2
    sid = w["seats"]["A1"]
    assert api.lock(c["token"], w["show_id"], [sid]).status_code == 200
    assert api.lock(c["token"], w["show_id"], [sid]).status_code == 200
    r = api.lock(c["token"], w["show_id"], [sid])
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}


def test_inactive_seat_cannot_be_locked(api):
    w = api.world()
    c = api.customer()
    api.c.put(f"/seats/{w['seats']['B3']}/status", headers=api.h(w["admin"]), json={"is_active": False})
    assert api.lock(c["token"], w["show_id"], [w["seats"]["B3"]]).status_code == 409


def test_concurrent_locks_only_one_winner(api):
    """Many customers racing for the same seat: exactly one must win."""
    w = api.world()
    customers = [api.customer() for _ in range(6)]
    results = []

    def attempt(cust):
        results.append(api.lock(cust["token"], w["show_id"], [w["seats"]["B1"]]).status_code)

    threads = [threading.Thread(target=attempt, args=(c,)) for c in customers]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(200) == 1
    assert all(code in (200, 409) for code in results)


def test_best_seat_suggestion(api):
    w = api.world(rows=("A",), per_row=6)
    c = api.customer()
    r = api.c.get(f"/shows/{w['show_id']}/suggest-seats", params={"count": 2}, headers=api.h(c["token"]))
    assert r.status_code == 200
    first = r.json()["suggestions"][0]
    assert len(first["seat_ids"]) == 2
