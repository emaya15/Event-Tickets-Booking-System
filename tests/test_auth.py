from datetime import timedelta

from app.auth.security import create_access_token


def test_public_registration_only_customer_and_organizer(api):
    for role in ("Admin", "Staff"):
        r = api.c.post(
            "/auth/register",
            json={"name": "Bad Actor", "email": f"{role}@x.com", "phone": "9876543210", "password": "Passw0rdX", "role": role},
        )
        assert r.status_code == 403
    assert api.customer()["token"]


def test_validation_errors_are_422_and_consistent(api):
    r = api.c.post("/auth/register", json={"name": "A", "email": "nope", "phone": "123", "password": "short"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_duplicate_email_is_409(api):
    u = api.customer()
    r = api.c.post(
        "/auth/register",
        json={"name": "Dup", "email": u["email"], "phone": "9876543210", "password": "Passw0rdX", "role": "Customer"},
    )
    assert r.status_code == 409


def test_login_and_me_and_bad_token(api):
    u = api.customer()
    assert api.c.get("/auth/me", headers=api.h(u["token"])).json()["email"] == u["email"]
    assert api.c.get("/auth/me").status_code == 401
    assert api.c.get("/auth/me", headers=api.h("garbage")).status_code == 401
    assert api.c.post("/auth/login", json={"email": u["email"], "password": "WrongPass1"}).status_code == 401


def test_expired_token_is_401(api):
    u = api.customer()
    token = create_access_token(u["id"], "Customer", expires_minutes=-1)
    r = api.c.get("/auth/me", headers=api.h(token))
    assert r.status_code == 401


def test_inactive_user_cannot_login(api):
    admin = api.admin()
    u = api.customer()
    assert api.c.put(f"/admin/users/{u['id']}/active", headers=api.h(admin), json={"is_active": False}).status_code == 200
    r = api.c.post("/auth/login", json={"email": u["email"], "password": "Passw0rdX"})
    assert r.status_code == 403


def test_role_enforcement_403(api):
    cust = api.customer()
    assert api.c.post("/venues", headers=api.h(cust["token"]), json={"name": "X", "city": "Y", "address": "Z"}).status_code == 403
    assert api.c.post("/admin/staff", headers=api.h(cust["token"]), json={}).status_code in (403, 422)


def test_unapproved_organizer_cannot_create_events(api):
    org = api.register("Organizer")
    r = api.c.post(
        "/events",
        headers=api.h(org["token"]),
        json={"title": "Show", "category": "Movie", "language": "Hindi", "duration_minutes": 90, "age_rating": "U"},
    )
    assert r.status_code == 403


def test_login_rate_limit_429_with_retry_after(api):
    from app.config import settings

    settings.LOGIN_RATE_LIMIT = 3
    for _ in range(3):
        api.c.post("/auth/login", json={"email": "x@test.com", "password": "Whatever1"})
    r = api.c.post("/auth/login", json={"email": "x@test.com", "password": "Whatever1"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}
