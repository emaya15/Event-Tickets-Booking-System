"""Reusable authentication / authorization dependencies."""
from typing import Optional

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.database import get_db
from app.models import User
from app.utils.constants import Role
from app.utils.errors import Forbidden, Unauthorized

# auto_error=False so that we can return our own consistent 401 body.
_bearer = HTTPBearer(auto_error=False)


def user_from_token(db: Session, token: str) -> User:
    """Validate a JWT and return the active user it belongs to (raises Unauthorized)."""
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Token has expired", headers={"WWW-Authenticate": "Bearer"})
    except (jwt.PyJWTError, KeyError, ValueError):
        raise Unauthorized("Invalid authentication token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User not found or inactive", headers={"WWW-Authenticate": "Bearer"})
    return user


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise Unauthorized("Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user_from_token(db, creds.credentials)


def get_optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer), db: Session = Depends(get_db)
) -> Optional[User]:
    """For public endpoints that behave differently for logged-in users."""
    if creds is None:
        return None
    return user_from_token(db, creds.credentials)


def require_roles(*roles: Role):
    """Dependency factory: ``Depends(require_roles(Role.ADMIN, Role.ORGANIZER))``."""
    allowed = {r.value for r in roles}

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise Forbidden("You do not have permission to perform this action")
        return user

    return checker


require_admin = require_roles(Role.ADMIN)
require_customer = require_roles(Role.CUSTOMER)
require_staff = require_roles(Role.STAFF)
