import io
from datetime import date

import pytest

from models import Document

FAKE_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"


def _register(client, email, name):
    resp = client.post("/auth/register", json={"email": email, "password": "testpass123", "name": name})
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    return headers, user_id


@pytest.fixture()
def user_a(client):
    return _register(client, "user-a@example.com", "User A")


@pytest.fixture()
def user_b(client):
    return _register(client, "user-b@example.com", "User B")


@pytest.fixture()
def user_b_document(client, db_session, user_b, monkeypatch):
    """A real document owned by user B, created through the actual upload
    endpoint (not inserted directly), so ownership is set exactly the way it
    would be in production."""
    _, user_b_id = user_b
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "User B's private invoice text")
    monkeypatch.setattr("processing.classify_text", lambda raw_text: ("Invoice", "reasoning"))
    monkeypatch.setattr(
        "processing.extract_structured_data",
        lambda category, raw_text: {"vendor_name": "B Corp", "due_date": "2026-12-01"},
    )

    b_headers, _ = user_b
    files = {"file": ("user-b-private.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    resp = client.post("/documents/upload", headers=b_headers, files=files)
    doc_id = resp.json()["id"]

    doc = db_session.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert str(doc.user_id) == user_b_id
    return doc_id


def test_user_a_does_not_see_user_bs_document_in_list(client, user_a, user_b_document):
    a_headers, _ = user_a
    docs = client.get("/documents", headers=a_headers).json()
    assert docs == []


def test_user_b_does_see_their_own_document_in_list(client, user_b, user_b_document):
    b_headers, _ = user_b
    docs = client.get("/documents", headers=b_headers).json()
    assert len(docs) == 1
    assert docs[0]["id"] == user_b_document


def test_user_a_gets_404_not_403_fetching_user_bs_document(client, user_a, user_b_document):
    """404, not 403 — a document's existence shouldn't be leaked to a user who
    doesn't own it (see CLAUDE.md's Per-user document isolation section)."""
    a_headers, _ = user_a
    resp = client.get(f"/documents/{user_b_document}", headers=a_headers)
    assert resp.status_code == 404


def test_user_a_cannot_delete_user_bs_document(client, db_session, user_a, user_b_document):
    a_headers, _ = user_a
    resp = client.delete(f"/documents/{user_b_document}", headers=a_headers)
    assert resp.status_code == 404

    # The document must genuinely survive the attempt, not just return 404
    # while quietly deleting it anyway.
    still_there = db_session.query(Document).filter(Document.id == user_b_document).first()
    assert still_there is not None


def test_user_a_cannot_reprocess_user_bs_document(client, user_a, user_b_document):
    a_headers, _ = user_a
    resp = client.post(f"/documents/{user_b_document}/reprocess", headers=a_headers)
    assert resp.status_code == 404


def test_user_a_cannot_chat_with_user_bs_document(client, user_a, user_b_document):
    a_headers, _ = user_a
    resp = client.post(
        f"/documents/{user_b_document}/chat", headers=a_headers, json={"question": "What's the total?"}
    )
    assert resp.status_code == 404


def test_user_a_cannot_get_download_url_for_user_bs_document(client, user_a, user_b_document):
    a_headers, _ = user_a
    resp = client.get(f"/documents/{user_b_document}/download-url", headers=a_headers)
    assert resp.status_code == 404


def test_user_a_does_not_see_user_bs_document_in_deadlines(client, db_session, user_a, user_b, user_b_document):
    doc = db_session.query(Document).filter(Document.id == user_b_document).first()
    doc.deadline_date = date.today()
    db_session.add(doc)
    db_session.commit()

    a_headers, _ = user_a
    b_headers, _ = user_b

    assert client.get("/documents/deadlines", headers=a_headers).json() == []
    b_deadlines = client.get("/documents/deadlines", headers=b_headers).json()
    assert len(b_deadlines) == 1
    assert b_deadlines[0]["id"] == user_b_document


def test_each_users_cache_entry_is_independent(client, user_a, user_b, user_b_document):
    """Regression test for the cache keying itself, not just the underlying
    query — the documents_cache key includes user id (see CLAUDE.md's Caching
    section), so user A's cached (empty) GET /documents must never leak into
    user B's request for the same route, or vice versa, even though both hit
    the same in-process cache object."""
    a_headers, _ = user_a
    b_headers, _ = user_b

    assert client.get("/documents", headers=a_headers).json() == []
    b_docs = client.get("/documents", headers=b_headers).json()
    assert len(b_docs) == 1
    # Re-fetching A's (now warm) cache entry still must not have picked up B's document.
    assert client.get("/documents", headers=a_headers).json() == []


def test_deleting_an_account_deletes_its_own_documents(client, db_session, user_b, user_b_document):
    """See CLAUDE.md's Settings/Per-user document isolation sections — account
    deletion now has a real relationship to act on, unlike before user_id
    existed."""
    b_headers, _ = user_b
    resp = client.delete("/auth/me", headers=b_headers)
    assert resp.status_code == 204

    assert db_session.query(Document).filter(Document.id == user_b_document).first() is None
