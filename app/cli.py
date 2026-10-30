"""Command line helpers.

    python -m app.cli create-admin --email admin@example.com --name "Site Admin" --phone 9000000000
        (the password is read from the ADMIN_PASSWORD env var or prompted securely)
"""
import argparse
import getpass
import os
import sys

from app.database import SessionLocal
from app.schemas.auth import validate_password, validate_phone
from app.services.auth_service import create_admin


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="Create an Admin account")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Platform Admin")
    p.add_argument("--phone", default="9000000000")
    args = parser.parse_args()

    password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    try:
        validate_phone(args.phone)
        validate_password(password)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        admin = create_admin(db, args.email, password, args.name, args.phone)
    finally:
        db.close()
    print("Admin created." if admin else "An account with this email already exists - nothing changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
