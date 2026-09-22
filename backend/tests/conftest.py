import os

# Must run before any backend module is imported — database.py, storage.py, and
# classification.py/chat.py/structured_extraction.py all build clients (engine,
# Supabase, Groq) at *import* time, from whatever's in os.environ right then. This
# points the app at a disposable test database (see the "Testing" section in
# CLAUDE.md for how to start one) instead of the real Supabase instance, and fills
# in placeholder values for the other required vars so the test suite never
# depends on a local .env existing (important for CI, which has none).
os.environ["SUPABASE_DB_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:15432/documentos_test"
)
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-not-for-production-use"
os.environ["GROQ_API_KEY"] = "test-groq-key-unused-all-groq-calls-are-mocked"
os.environ["SUPABASE_URL"] = "https://test-project.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-role-key-unused-storage-calls-are-mocked"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
# Explicitly empty (not just left unset) so a real SENTRY_DSN sitting in a
# developer's local .env can never cause a test run to initialize a real Sentry
# client and report a deliberately-raised test exception (e.g.
# test_upload_classification_failure_sanitizes_error_message's fake secret) to
# an actual project.
os.environ["SENTRY_DSN"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from analytics_router import analytics_cache  # noqa: E402
from database import Base, SessionLocal, engine  # noqa: E402
from main import app, documents_cache, deadlines_cache, signed_url_cache  # noqa: E402
from rate_limit import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Creates tables straight from the ORM models (Base.metadata), not by running
    Alembic migrations — the tests exercise application behavior, not migration
    correctness, and this is faster/simpler with one fewer moving part."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncates between every test instead of wrapping each test in a rolled-back
    transaction — the app's own session (opened fresh per request via get_db())
    and any db_session a test uses are different connections to the same real
    Postgres database, so a rollback-based approach would need SAVEPOINT-nesting
    trickery for no real benefit at this test suite's size."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE documents, users, tool_files RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(autouse=True)
def _clean_caches():
    """documents_cache/deadlines_cache/signed_url_cache/analytics_cache (see
    CLAUDE.md's Caching section) are module-level singletons, so without this,
    a cached response from one test could leak into the next — which now runs
    against a just-truncated table it never touched."""
    documents_cache.clear()
    deadlines_cache.clear()
    signed_url_cache.clear()
    analytics_cache.clear()
    yield


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Rate limiting (see the "Rate limiting" section in CLAUDE.md) is real
    application behavior with its own dedicated test (test_auth.py's
    test_login_rate_limited), which turns this back on for just that one test.
    Everywhere else, tests shouldn't fail because an earlier test in the same run
    already used up the /auth/login quota against the same in-memory limiter."""
    limiter.enabled = False
    yield
    limiter.enabled = False


@pytest.fixture()
def db_session():
    """A direct DB session for tests to seed/inspect rows outside of the API —
    e.g. test_deadlines.py inserts documents with specific deadline_dates directly,
    then asserts on what GET /documents/deadlines returns for them."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def storage_objects(monkeypatch):
    """In-memory stand-in for the tool-outputs Supabase Storage prefix — shared
    by test_tools.py and test_editor_chat.py. Patches each module's own
    imported name (tools_common's upload/delete, tools_router's
    create_signed_url, editor_chat's download — used when a chat turn
    references a file produced by an earlier turn), per this project's
    "patch where a name is used" monkeypatch convention."""
    objects: dict = {}

    def fake_upload(data, key, content_type):
        objects[key] = data
        return key

    def fake_download(key):
        return objects[key]

    def fake_delete(key):
        objects.pop(key, None)

    monkeypatch.setattr("tools_common.upload_file_to_storage", fake_upload)
    monkeypatch.setattr("tools_common.delete_file_from_storage", fake_delete)
    monkeypatch.setattr(
        "tools_router.create_signed_url",
        lambda key, expires_in: f"https://fake-signed-url.test/{key}?expires_in={expires_in}",
    )
    monkeypatch.setattr("editor_chat.download_file_from_storage", fake_download)
    return objects
