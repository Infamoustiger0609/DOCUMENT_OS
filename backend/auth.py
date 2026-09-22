import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import sentry_sdk
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from config import JWT_ALGORITHM, JWT_EXPIRES_MINUTES, JWT_SECRET_KEY
from database import get_db
from models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

PASSWORD_MIN_LENGTH = 8


def validate_password_strength(password: str) -> Optional[str]:
    """Returns an error message if the password is too weak, else None. Used by
    both registration and change-password so the two never drift apart. Beyond
    the minimum length, requires at least one letter and one number — enough to
    reject trivially-guessable all-digit ("12345678") or all-letter ("password")
    values without imposing a symbol/uppercase rule that mostly just annoys users."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if not re.search(r"[A-Za-z]", password):
        return "Password must contain at least one letter."
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one number."
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(subject: str, token_version: int = 0) -> str:
    """`token_version` is embedded as the "tv" claim and checked against the
    user's own `token_version` column in get_current_user() below — this is
    what lets POST /auth/change-password invalidate every previously-issued
    token immediately (by bumping the column) instead of a stolen token
    remaining valid for its full ~24h natural lifetime regardless (a real
    gap found in a security audit — see CLAUDE.md's Security section)."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES)
    payload = {"sub": subject, "tv": token_version, "exp": expires_at}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_version = payload.get("tv", 0)
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    # A token issued before the user's last password change carries a stale
    # "tv" claim — reject it exactly like an expired/invalid token, not with
    # a different error, so this doesn't leak whether the mismatch is due to
    # a password change versus any other invalid-token reason.
    if token_version != user.token_version:
        raise credentials_exception

    # Scopes any Sentry event captured for the rest of this request to this
    # user (see error_tracking.py) — a safe no-op if Sentry isn't configured.
    # email is deliberately left out; id alone is enough to look a user up.
    sentry_sdk.set_user({"id": str(user.id)})
    return user
