def test_event_validation_duration(api):
    w = api.world()
    r = api.c.post(
        "/events",
        headers=api.h(w["org"]["token"]),
        json={"title": "Too short", "category": "Movie", "language": "Hindi", "duration_minutes": 10, "age_rating": "U"},
    )
    assert r.status_code == 422


def test_show_requires_prices_for_every_category(api):
    w = api.world()
    r = api.c.post(
        f"/events/{w['event_id']}/shows",
        headers=api.h(w["org"]["token"]),
        json={"screen_id": w["screen_id"], "show_date": "2099-01-01", "start_time": "10:00:00", "prices": {"Silver": 100}},
    )
    assert r.status_code == 400
    assert "Missing" in r.json()["detail"]


def test_show_must_be_in_future_and_not_overlap(api):
    w = api.world()
    past = api.c.post(
        f"/events/{w['event_id']}/shows",
        headers=api.h(w["org"]["token"]),
        json={"screen_id": w["screen_id"], "show_date": "2020-01-01", "start_time": "10:00:00", "prices": {"Silver": 100, "Gold": 200}},
    )
    assert past.status_code == 400
    shows = api.c.get(f"/events/{w['event_id']}/shows").json()
    s = shows[0]
    # Same start time on the same screen -> overlap (409)
    clash = api.c.post(
        f"/events/{w['event_id']}/shows",
        headers=api.h(w["org"]["token"]),
        json={"screen_id": w["screen_id"], "show_date": s["show_date"], "start_time": s["start_time"], "prices": {"Silver": 100, "Gold": 200}},
    )
    assert clash.status_code == 409


def test_organizer_cannot_manage_others_events(api):
    w = api.world()
    other = api.organizer(w["admin"])
    r = api.c.put(f"/events/{w['event_id']}", headers=api.h(other["token"]), json={"title": "Hijack"})
    assert r.status_code == 403


def test_customers_see_only_published_events(api):
    w = api.world()
    api.c.post(
        "/events",
        headers=api.h(w["org"]["token"]),
        json={"title": "Secret Draft", "category": "Comedy", "language": "Hindi", "duration_minutes": 60, "age_rating": "U"},
    )
    titles = [e["title"] for e in api.c.get("/events").json()["items"]]
    assert "Space Movie" in titles and "Secret Draft" not in titles


def test_event_filters_sorting_pagination_and_cache(api):
    w = api.world()
    r = api.c.get("/events", params={"city": "Hyderabad", "category": "Movie", "language": "Telugu", "skip": 0, "limit": 10})
    assert r.status_code == 200 and r.json()["total"] == 1
    assert api.c.get("/events", params={"city": "Mumbai"}).json()["total"] == 0
    assert api.c.get("/events", params={"title": "space"}).json()["total"] == 1
    assert api.c.get("/events", params={"sort_by": "bogus"}).status_code == 400
    assert api.c.get("/events", params={"limit": 1000}).status_code == 422
    # second call is served from the Redis cache and still correct
    assert api.c.get("/events", params={"title": "space"}).json()["total"] == 1
    # creating a new published event invalidates the cache
    api.c.post(
        "/events",
        headers=api.h(w["org"]["token"]),
        json={"title": "Space Two", "category": "Movie", "language": "Telugu", "duration_minutes": 90, "age_rating": "U", "status": "Published"},
    )
    assert api.c.get("/events", params={"title": "space"}).json()["total"] == 2


def test_prices_cannot_change_after_confirmed_booking(api):
    w = api.world()
    cust = api.customer()
    api.confirmed_booking(w, cust)
    r = api.c.put(f"/shows/{w['show_id']}", headers=api.h(w["org"]["token"]), json={"prices": {"Silver": 999, "Gold": 999}})
    assert r.status_code == 409


def test_bulk_seats_unique_and_layout_marks_inactive(api):
    w = api.world()
    admin = w["admin"]
    dup = api.c.post(
        f"/screens/{w['screen_id']}/seats/bulk", headers=api.h(admin),
        json={"row_labels": ["A"], "seats_per_row": 2, "category": "Silver"},
    )
    assert dup.status_code == 409
    seat = w["seats"]["A3"]
    assert api.c.put(f"/seats/{seat}/status", headers=api.h(admin), json={"is_active": False}).status_code == 200
    layout = api.c.get(f"/screens/{w['screen_id']}/layout", headers=api.h(admin)).json()
    row_a = next(r for r in layout["rows"] if r["row_label"] == "A")
    assert [s["seat_number"] for s in row_a["seats"]] == [1, 2, 3, 4, 5, 6]
    assert row_a["seats"][2]["status"] == "Unavailable"
