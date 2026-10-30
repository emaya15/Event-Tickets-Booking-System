# Event & Movie Ticket Booking System (FastAPI)

A production-style backend: **FastAPI + MySQL (SQLAlchemy 2.x, Alembic) + Redis + JWT + WebSockets + APScheduler**.
It covers all 15 levels of the specification plus the bonus features (Razorpay test mode, dynamic pricing,
best-seat suggestion, coupons, tests, Docker).

> **Time zone:** every timestamp, show date/time and booking window is **UTC**. When creating shows, send UTC date/time.

---------------------------------------------------------------------

## 1. Overview and architecture

```
Router (HTTP / WS)  ->  Service (business rules)  ->  SQLAlchemy models  ->  MySQL
                              |                          
                              +--> Redis (seat locks, cache, rate limits, job locks)
                              +--> WebSocket manager (live seat map)
                              +--> Email service (SMTP / mock log)
```

| Folder | Purpose |
|---|---|
| `app/routers` | Thin HTTP layer: validation, auth dependencies, call a service |
| `app/services` | All business logic (locking, booking, payments, refunds, reports ...) |
| `app/models`, `app/schemas` | SQLAlchemy tables / Pydantic request & response models |
| `app/auth` | bcrypt hashing, JWT, reusable role dependencies |
| `app/websockets` | Live seat-map endpoint and connection manager |
| `app/jobs` | APScheduler jobs and the dedicated scheduler runner |
| `alembic/versions` | Migrations (the **only** way the schema is created) |
| `static/seat_map_test.html` | Browser page to watch seats update live |
| `tests/` | Pytest suite (SQLite + fakeredis, no servers needed) |

Seat life cycle: `Available -> Locked (Redis, 10 min) -> Booked (DB, after payment)`.
A booking is created from your locks (*Pending Payment*), paid, then *Confirmed*; unpaid bookings become *Expired*.

## 2. Features by level

1. **Setup** - MySQL + Redis config from env vars, Alembic initial migration (all tables, FKs, indexes, constraints), `created_at/updated_at` everywhere, `.env.example`, `.gitignore`.
2. **Auth** - register/login/me, roles Admin/Organizer/Customer/Staff, organizer approval, admin-created Staff bound to one venue, bcrypt, 30-minute JWT, 401/403 rules, inactive users blocked.
3. **Venues/screens/seats** - Admin only; bulk seat creation; unique `(screen,row,number)`; grouped layout; inactive seats shown *Unavailable*.
4. **Events/shows/pricing** - categories, future-only shows, end time from duration, booking closes 30 min before start, 15-minute cleaning gap, price per seat category required (> 0), prices frozen after first confirmed booking.
5. **Redis locking** - `SET NX EX 600`, key `lock:show:<show>:seat:<seat>`, value `<user_id>:<price>`; all-or-nothing (409 lists unavailable seats); max 10 seats/show; single-empty-seat rule; owner-only release; ownership re-checked at booking and payment.
6. **WebSocket live map** - `WS /ws/shows/{id}/seats?token=`; full map on connect; lock / release / expiry / booking / cancellation deltas; your own locks show as `Yours`; `GET /seat-map-test`.
7. **Bookings & bill** - `BKG-YYYYMMDD-0001` numbers; ticket total, Rs.30 fee per ticket, 18% GST on (tickets + fee); prices stored per seat; unique constraint so a seat can be confirmed only once per show.
8. **Mock payments** - server-derived amount; one transaction saves payment + confirms booking + reserves seats + creates ticket; failures can be retried; duplicate payments impossible; **late payment** after expiry confirms if seats are free, otherwise auto full refund and cancel.
9. **PDF tickets / QR / scanning** - ReportLab PDF with HMAC-signed QR; Staff-only scan at their own venue, from 1 hour before start until the show ends, single use (atomic `UPDATE ... WHERE is_used=0`), `409 Ticket already used at ...`; entries report.
10. **Cancellations/refunds/waitlist** - >24h 100% of ticket total, 2-24h 50%, <2h not allowed (fee + GST never refunded); organizer show cancellation refunds 100% incl. fee/GST and notifies by email + live message; waitlist for sold-out shows notified in queue order.
11. **Email + jobs** - BackgroundTasks + SMTP (mock log when `SMTP_HOST` is empty); APScheduler: expire bookings (1 min), reminders (30 min, once per booking), complete shows (15 min), complete events (daily).
12. **Cache & rate limits** - event list/detail cached 5 min and invalidated on changes (seat map never cached); lock 5/min/customer, login 5/min/IP, `429` + `Retry-After`.
13. **Validation/errors** - unique email/booking number/transaction id, 10-digit phone, duration 30-300, consistent `{"detail","code"}` errors, no stack traces leaked.
14. **Pagination/search/sort** - `skip`/`limit` everywhere; event filters (title, city, category, language, dates, price); `sort_by`/`order`; booking filters.
15. **Reports** - dashboard, event sales, occupancy, top events, refunds, Excel export.

**Bonus:** Razorpay test mode + signed webhook (idempotent), dynamic pricing (+10% above 80% booked), best-seat suggestion, coupons, tests, Docker.

## 3. Prerequisites
Python **3.9 - 3.12**, MySQL 8, Redis 6+ (Docker optional).

## 4. Windows PowerShell setup
```powershell
cd event_ticket_booking
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
```
## 5. Install dependencies
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```
## 6. Create the MySQL database
```sql
CREATE DATABASE ticket_booking CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ticket_user'@'localhost' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON ticket_booking.* TO 'ticket_user'@'localhost';
```
## 7. Redis
Windows: use Docker (`docker run -d -p 6379:6379 redis:7`) or WSL (`sudo apt install redis-server && sudo service redis-server start`).
Linux/macOS: `redis-server`. Test: `redis-cli ping` -> `PONG`.

## 8. `.env`
```powershell
copy .env.example .env
```
Edit `DATABASE_URL`, `REDIS_URL`, and generate secrets: `python -c "import secrets; print(secrets.token_urlsafe(48))"` for `JWT_SECRET` and `QR_SIGNING_SECRET`. Never commit `.env`.

## 9. Migrations
```powershell
alembic upgrade head          # creates every table
alembic current               # shows 0001_initial
```
The app never calls `create_all()`; change the schema only through new Alembic revisions.

## 10. Initialise the Admin account securely
Option A - at startup: set `ADMIN_EMAIL` and a strong `ADMIN_PASSWORD` in `.env`; the Admin is created once if missing (then remove the password from `.env`).
Option B - command: `python -m app.cli create-admin --email you@example.com --name "Site Admin"` (password prompted, or taken from the `ADMIN_PASSWORD` env var).
No credentials are hard-coded.

## 11. Run
```powershell
uvicorn app.main:app --reload
```
Multiple workers: run the API with `RUN_SCHEDULER=false` and start **one** scheduler process: `python -m app.jobs.run_scheduler` (a Redis lock also prevents duplicate runs).

## 12. Swagger
Open http://localhost:8000/docs . Click **Authorize**, paste the `access_token` from `/auth/login` (no `Bearer ` prefix needed).

## 13. Creating and logging in as every role
| Role | How to create | Login |
|---|---|---|
| Admin | step 10 only (never via API) | `POST /auth/login` |
| Organizer | `POST /auth/register` with `"role":"Organizer"`, then Admin calls `PUT /admin/organizers/{id}/approve` | `POST /auth/login` |
| Customer | `POST /auth/register` with `"role":"Customer"` | `POST /auth/login` |
| Staff | Admin calls `POST /admin/staff` with `venue_id` | `POST /auth/login` |

Demo accounts: create them yourself with the calls above using throw-away local passwords (e.g. generate with `secrets.token_urlsafe(12)`). The project ships **no** default credentials. Passwords need >= 8 chars with letters and digits.

## 14. Example workflow (copy into Swagger)
1. Admin login -> `POST /venues` `{"name":"PVR","city":"Hyderabad","address":"Road 1"}`
2. `POST /venues/1/screens` `{"name":"Screen 1","total_seats":60}`
3. `POST /screens/1/seats/bulk` `{"row_labels":["A","B"],"seats_per_row":8,"category":"Silver"}` (repeat with `"C"` and `"Gold"`)
4. Register + approve an Organizer, log in, `POST /events` `{"title":"Space Movie","category":"Movie","language":"Telugu","duration_minutes":120,"age_rating":"UA","status":"Published"}`
5. `POST /events/1/shows` `{"screen_id":1,"show_date":"2026-12-01","start_time":"18:00:00","prices":{"Silver":150,"Gold":250}}` (UTC, in the future)
6. Register a Customer, log in. `GET /shows/1/seats` to see seat ids.
7. `POST /shows/1/lock-seats` `{"seat_ids":[1,2]}`
8. `POST /bookings` `{"show_id":1,"seat_ids":[1,2]}`
9. `POST /bookings/1/pay` `{"payment_method":"UPI","simulate_status":"Success"}`
10. `GET /bookings/1/ticket` (download the PDF; the QR text is `<booking_number>.<signature>`)
11. Create Staff (`POST /admin/staff`), log in as Staff, within one hour of start: `POST /tickets/scan` `{"qr_data":"BKG-...-0001.<signature>"}`
    (for testing, move the show close to now with the organizer's `PUT /shows/{id}` or edit `start_at` in the DB). A second scan returns **409**.

## 15. WebSocket testing
**HTML page:** open http://localhost:8000/seat-map-test, enter the show id and a login token, click *Connect*. Lock seats in Swagger (another customer) and watch the grid change.
**Postman:** New -> WebSocket -> URL `ws://localhost:8000/ws/shows/1/seats?token=<ACCESS_TOKEN>` -> Connect. You immediately receive `{"type":"seat_map",...}`; later `{"type":"seat_update","event":"locked|released|expired|booked|cancelled","seats":[{"seat_id":1,"status":"Locked"}]}`. Seats you locked appear as `Yours`. Invalid tokens are refused (close code 4401/403).
Expiry is detected by a lightweight watcher (every 3 s).

## 16. Testing locking, caching, rate limits
* **Locking:** call `lock-seats` as two customers for the same seat -> second gets `409` with `unavailable_seats`. `redis-cli ttl lock:show:1:seat:1` shows ~600.
* **Single-seat rule:** lock A1,A2 then try A4 -> `409` (would strand A3).
* **Cache:** `GET /events` twice, `redis-cli keys "cache:events:*"`; creating/updating an event or show clears them.
* **Rate limit:** call `lock-seats` 6 times in a minute (-> `429`, `Retry-After`); login 6 times with a wrong password.

## 17. SMTP / mock email
Leave `SMTP_HOST` empty -> emails are logged as `[MOCK EMAIL]` in the console. For real mail set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (Gmail needs an app password). Email errors are logged and never undo a booking.

## 18. Scheduled jobs
| Job | Every | Effect |
|---|---|---|
| expire_bookings | 1 min | Pending Payment past `expires_at` -> Expired, locks freed, waitlist processed |
| show_reminders | 30 min | email for shows starting within 3 h (once per booking) |
| complete_shows | 15 min | ended shows -> Completed |
| complete_events | daily | events with all shows Completed -> Completed |

Jobs start with the app and stop cleanly on shutdown. Troubleshooting: check logs for `app.scheduler`; ensure Redis is up; with several workers use the dedicated scheduler process.

## 19. Pytest
```powershell
pytest                 # uses SQLite + fakeredis; no MySQL/Redis needed
pytest tests/test_seat_locking.py -k concurrent
```
Coverage: auth & roles, validation, catalog rules, seat locking incl. concurrency and single-seat rule, bill maths, success/failed/late payments, cancellation policy, show cancellation, waitlist, ticket scanning & authorization, reports, rate limits, coupons, dynamic pricing, Razorpay webhook. (The tests create their schema directly on SQLite; production uses Alembic.)

## 20. Excel export
`GET /reports/event-sales/export?from_date=2026-01-01&to_date=2026-12-31` (Admin, or Organizer for own events) downloads an `.xlsx`. In Swagger use *Try it out* -> *Download file*.

## 21. Docker
```powershell
copy .env.example .env      # set secrets; compose overrides DATABASE_URL / REDIS_URL
docker compose up --build
```
Services: `mysql` (persistent volume, health check), `redis`, `migrate` (runs `alembic upgrade head` then exits), `api` (health check on `/health`), `scheduler` (single job runner). Optionally set `MYSQL_PASSWORD` in `.env` (used by both MySQL and the API URL). Docker is not needed for local development.

## 22. Environment variables, assumptions, limitations, common errors
Variables: see `.env.example` (all documented there).

**Assumptions**
* Times are UTC; shows are created with UTC date/time.
* "Sold out" (waitlist) = no seat currently Available (booked or held by a lock).
* Coupon discount reduces the ticket total before GST; refunds on customer cancel use the ticket total after discount.
* Booking list date filters apply to the booking creation date; event/occupancy report ranges apply to show dates (default last 30 days to +90 days).
* Revenue = Success + Refunded payments (collected) minus refund records; failed payments never count.
* Refunds and payments are mock (no real money moves). Razorpay is optional and test-mode only.

**Limitations**
* WebSocket connections are per process; with several workers instant pushes reach only clients on the same worker, the 3-second watcher covers the rest.
* The test-suite uses SQLite, which ignores `SELECT ... FOR UPDATE`; row locking is exercised on MySQL only. Unique constraints still enforce correctness in both.
* The `pay` endpoint's `simulate_status` is for testing; remove it (or restrict by `APP_ENV`) before real payments.

**Common errors**
* `Can't connect to MySQL` -> check `DATABASE_URL`, that MySQL runs and the DB exists, then `alembic upgrade head`.
* `Table ... doesn't exist` -> migrations not applied.
* `503 redis_unavailable` -> Redis is not running / wrong `REDIS_URL`.
* `401` -> missing/expired token (30 min); `403` -> wrong role or unapproved organizer.
* `409 ... single empty seat` -> choose seats that do not leave a lone gap.
