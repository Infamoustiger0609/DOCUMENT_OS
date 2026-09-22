import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import JWT_ALGORITHM, JWT_SECRET_KEY

limiter = Limiter(key_func=get_remote_address, headers_enabled=True)


def user_or_ip_key(request: Request) -> str:
    """Per-user rate-limit key for authenticated routes (e.g. upload), so one
    office/NAT sharing a public IP doesn't share a single quota. Decodes the JWT
    straight from the header rather than depending on get_current_user having
    already run, since slowapi's key_func only ever receives the raw Request.
    Falls back to per-IP for a missing/invalid token — that request 401s from
    the real auth dependency regardless, so an IP-based limit is just a sane
    fallback bucket, not a security gap."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except jwt.PyJWTError:
            pass
    return get_remote_address(request)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # {"detail": ...} matches every other error response in this app (see
    # frontend's parseErrorDetail in auth-context.tsx), instead of slowapi's
    # default {"error": ...} shape.
    response = JSONResponse(
        {"detail": "Too many requests. Please wait a moment and try again."},
        status_code=429,
    )
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
