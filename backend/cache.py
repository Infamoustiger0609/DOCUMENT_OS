import threading
import time
from typing import Any, Hashable, Optional


class TTLCache:
    """A minimal in-process, thread-safe TTL cache. Enough at this app's current
    scale (a single uvicorn process, no --workers — see CLAUDE.md's Rate limiting
    section for the same reasoning applied to slowapi's storage) — Redis is the
    natural next step if this ever needs to be shared across multiple processes
    or survive a restart, neither of which is true yet."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Shared, module-level instances (not per-router locals) — both main.py's
# /documents* endpoints and templates_router.py's POST /templates/{id}/generate
# (Phase 32, which also creates a real Document row) need to invalidate the
# same two caches. Living here, rather than in main.py, avoids main.py <->
# templates_router.py becoming a circular import (main.py already imports
# templates_router; templates_router importing back from main.py for these
# would be circular). See CLAUDE.md's Caching section for the full reasoning
# and invalidation strategy.
DOCUMENTS_CACHE_TTL_SECONDS = 60
documents_cache = TTLCache(ttl_seconds=DOCUMENTS_CACHE_TTL_SECONDS)
deadlines_cache = TTLCache(ttl_seconds=DOCUMENTS_CACHE_TTL_SECONDS)


def invalidate_document_list_caches() -> None:
    """Called after anything that changes what GET /documents or
    GET /documents/deadlines would return (upload, delete, reprocess, the
    background processing task completing, and — Phase 32 — learning a
    template from a sample or generating a new document from one) — clears
    both caches outright (every user's entries, not just the acting user's)
    rather than trying to evict individual entries. See CLAUDE.md's Caching
    section for why clearing everyone's cache over one user's write is still
    the right tradeoff at this app's scale."""
    documents_cache.clear()
    deadlines_cache.clear()
