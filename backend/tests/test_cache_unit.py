import time

from cache import TTLCache


def test_returns_none_for_missing_key():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_returns_cached_value_within_ttl():
    cache = TTLCache(ttl_seconds=60)
    cache.set("key", "value")
    assert cache.get("key") == "value"


def test_expires_after_ttl():
    cache = TTLCache(ttl_seconds=0.05)
    cache.set("key", "value")
    assert cache.get("key") == "value"
    time.sleep(0.1)
    assert cache.get("key") is None


def test_clear_removes_everything():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
