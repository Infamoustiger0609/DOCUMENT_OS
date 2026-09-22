import contextvars
import json
import logging
import sys

# Set once per request by main.py's request_context_middleware, read here by
# RequestIdFilter so every log line emitted while handling that request carries
# the same id — including lines logged deep inside processing.py, several calls
# away from the middleware itself. Contextvars propagate correctly through
# async/await and FastAPI's threadpool-offloaded sync endpoints, so this doesn't
# need to be threaded through every function call by hand.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# LogRecord attributes that logging itself always sets — used to detect which
# extra keys a call site actually added via logger.info(..., extra={...}), so
# those show up as real structured fields, not folded into the message string.
_STANDARD_LOG_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime", "request_id",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line instead of free-text — machine-parseable
    (greppable by request_id, ready to feed into a log aggregator later).
    Deliberately never includes a raw request body or any field that could carry
    a password, token, or a document's raw_text — every logger call in this
    codebase passes small identifiers (document ids, request paths, status
    codes), never those fields directly. See the Security section in CLAUDE.md
    for why error_message itself is already sanitized before it ever reaches a
    log call."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str: a logging call must never crash the app over a
        # non-JSON-serializable value someone passed via extra=.
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # main.py's request_context_middleware already logs one structured line per
    # request (method/path/status/duration, with a request_id) — silencing
    # uvicorn's own access log avoids a second, differently-shaped line for the
    # same event.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
