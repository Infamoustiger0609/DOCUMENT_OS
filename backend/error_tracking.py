from typing import Any, Optional

import sentry_sdk

from config import ENVIRONMENT, SENTRY_DSN

# Defense in depth on top of the app already never handing these fields to a
# logger (see the Security section in CLAUDE.md): Sentry's FastAPI/Starlette
# integration can attach request headers/bodies to an event, so this scrubs
# them too before anything leaves the process, in case a future endpoint ever
# does pass one of these through request state/extra context.
_SENSITIVE_KEYS = {
    "password",
    "current_password",
    "new_password",
    "authorization",
    "access_token",
    "token",
    "raw_text",
}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[Filtered]" if key.lower() in _SENSITIVE_KEYS else _scrub(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _before_send(event: dict, hint: dict) -> Optional[dict]:
    request = event.get("request")
    if request:
        headers = request.get("headers")
        if headers:
            request["headers"] = {
                key: ("[Filtered]" if key.lower() == "authorization" else val)
                for key, val in headers.items()
            }
        if "data" in request:
            request["data"] = _scrub(request["data"])
    if "extra" in event:
        event["extra"] = _scrub(event["extra"])
    return event


def init_error_tracking() -> None:
    """No-op when SENTRY_DSN isn't set (local dev, or before a Sentry project
    exists) — every sentry_sdk.* call elsewhere in the app (set_user, set_tag,
    the FastAPI/logging integrations' own capture_exception calls) is a safe
    no-op too when init() was never called, so nothing needs an `if SENTRY_DSN`
    guard at the call site, only here."""
    if not SENTRY_DSN:
        return
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        # Default (False): don't attach IPs/cookies/request bodies automatically.
        # Request context we do want (method, path, status) comes from the
        # FastAPI/Starlette integration's own instrumentation instead, and
        # before_send above scrubs it further.
        send_default_pii=False,
        before_send=_before_send,
    )
