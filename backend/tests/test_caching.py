import io
import uuid
from datetime import date

import pytest

from models import Document

FAKE_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "cache-tester@example.com", "password": "testpass123", "name": "Cache Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


def _make_doc(db_session, user_id, filename="test.pdf", **kwargs):
    doc = Document(
        user_id=user_id,
        filename=filename,
        storage_path=f"{uuid.uuid4()}.pdf",
        status=kwargs.get("status", "processed"),
        category=kwargs.get("category"),
        deadline_date=kwargs.get("deadline_date"),
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_get_documents_serves_stale_cache_within_ttl(client, db_session, auth_headers, current_user_id):
    """Proves GET /documents actually hits the cache (not just "happens to
    return the same thing") by mutating the DB directly, bypassing the API
    entirely (so no invalidation fires), and confirming the next call still
    returns the pre-mutation cached response."""
    first = client.get("/documents", headers=auth_headers).json()
    assert first == []

    _make_doc(db_session, current_user_id, filename="snuck-in-directly.pdf")

    second = client.get("/documents", headers=auth_headers).json()
    assert second == [], "expected the cached (stale) empty list, not a fresh DB read"


def test_get_documents_deadlines_serves_stale_cache_within_ttl(client, db_session, auth_headers, current_user_id):
    first = client.get("/documents/deadlines", headers=auth_headers).json()
    assert first == []

    _make_doc(db_session, current_user_id, filename="snuck-in.pdf", deadline_date=date.today())

    second = client.get("/documents/deadlines", headers=auth_headers).json()
    assert second == [], "expected the cached (stale) empty list, not a fresh DB read"


def test_upload_invalidates_the_documents_cache(client, auth_headers, monkeypatch):
    """The counterpart to the staleness tests above — a real write through the
    API (not a direct DB mutation) must invalidate the cache, or a user
    uploading a document wouldn't see it show up on their own registry."""
    assert client.get("/documents", headers=auth_headers).json() == []

    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "some text")
    monkeypatch.setattr("processing.classify_text", lambda raw_text: ("Other", "reasoning"))

    files = {"file": ("invoice.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    client.post("/documents/upload", headers=auth_headers, files=files)

    after_upload = client.get("/documents", headers=auth_headers).json()
    assert len(after_upload) == 1
    assert after_upload[0]["filename"] == "invoice.pdf"


def test_delete_invalidates_the_documents_cache(client, db_session, auth_headers, current_user_id):
    doc = _make_doc(db_session, current_user_id, filename="to-delete.pdf")
    assert len(client.get("/documents", headers=auth_headers).json()) == 1

    resp = client.delete(f"/documents/{doc.id}", headers=auth_headers)
    assert resp.status_code == 204

    assert client.get("/documents", headers=auth_headers).json() == []


def test_download_url_is_cached_within_its_validity_window(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    doc = _make_doc(db_session, current_user_id, filename="viewable.pdf")

    call_count = {"n": 0}

    def fake_create_signed_url(storage_path, expires_in):
        call_count["n"] += 1
        return f"https://fake-signed-url.example.com/{storage_path}?call={call_count['n']}"

    monkeypatch.setattr("main.create_signed_url", fake_create_signed_url)

    first = client.get(f"/documents/{doc.id}/download-url", headers=auth_headers)
    second = client.get(f"/documents/{doc.id}/download-url", headers=auth_headers)

    assert first.status_code == 200
    assert first.json() == second.json()
    assert call_count["n"] == 1, "expected the second request to be served from cache"
