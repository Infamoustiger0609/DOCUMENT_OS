"""Regression tests for the findings fixed in this app's security audit (see
CLAUDE.md's Security audit section): the Storage path-traversal bug,
tool_files being orphaned in Storage on account deletion, missing rate
limits on document chat/reprocess/signature-upload, JWTs staying valid
after a password change, the JWT_SECRET_KEY startup check, the new
audit_logs table, and sslmode enforcement on the Postgres connection.
"""

import io

import pymupdf as fitz
import pytest

from config import _validate_jwt_secret_key
from database import _connect_args_for
from models import AuditLog, Document, SignedDocument, ToolFile
from tools_common import _build_tool_storage_key

FAKE_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"


def _make_real_pdf(text: str = "test") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


# ---------------------------------------------------------------------------
# C1 — Storage path traversal
# ---------------------------------------------------------------------------


def test_tool_storage_key_never_embeds_a_traversal_sequence():
    """Regression test for a real, live-verified bug: Supabase Storage
    genuinely resolves "../" segments in an object key (confirmed directly
    against the real project during the audit — an upload landed outside its
    intended prefix). A crafted upload filename must never let the resulting
    Storage key escape this user's own tool-outputs/{user_id}/ prefix."""
    user_id = "11111111-1111-1111-1111-111111111111"
    key = _build_tool_storage_key(user_id, "../../../../etc/passwd.pdf")
    assert ".." not in key
    assert key.startswith(f"tool-outputs/{user_id}/")
    assert key.endswith(".pdf")


def test_tool_storage_key_uses_only_the_extension_from_output_filename():
    user_id = "22222222-2222-2222-2222-222222222222"
    key = _build_tool_storage_key(user_id, "resized_../../evil.jpg")
    assert ".." not in key
    assert key.endswith(".jpg")


def test_sign_document_storage_path_never_embeds_the_original_filename(client, db_session, monkeypatch):
    """The original document's own filename (fully user-controlled at upload
    time) must never be embedded in the signed PDF's Storage key — only in
    the display-only SignedDocument.filename column."""
    monkeypatch.setattr("signature_router.upload_file_to_storage", lambda data, key, content_type: key)
    monkeypatch.setattr("signature_router.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("signature_router.stamp_signature", lambda pdf_bytes, signature_bytes: FAKE_PDF_BYTES)

    resp = client.post(
        "/auth/register",
        json={"email": "sign-traversal@example.com", "password": "testpass123", "name": "Sign Test"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]

    from models import User

    user = db_session.query(User).filter(User.id == user_id).first()
    user.signature_storage_path = f"signatures/{user_id}/signature.png"
    db_session.add(user)
    db_session.commit()

    document = Document(
        user_id=user_id,
        filename="../../../../malicious_traversal_name.pdf",
        storage_path=f"{user_id}.pdf",
        status="processed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    resp = client.post(f"/documents/{document.id}/sign", headers=headers)
    assert resp.status_code == 200
    signed = db_session.query(SignedDocument).filter(SignedDocument.document_id == document.id).first()
    assert signed is not None
    assert ".." not in signed.storage_path
    assert signed.storage_path.startswith(f"signed-documents/{user_id}/")
    # The original (crafted) filename is still preserved for display purposes.
    assert "malicious_traversal_name" in signed.filename


# ---------------------------------------------------------------------------
# H1 — tool_files orphaned in Storage on account deletion
# ---------------------------------------------------------------------------


def test_account_deletion_removes_tool_file_storage_objects(client, db_session, storage_objects, monkeypatch):
    # main.py's delete_account() imports its own copy of delete_file_from_storage
    # directly from storage.py — a separate name binding from tools_common's own
    # imported copy (already patched by the shared storage_objects fixture), so
    # it needs patching here too, per this project's "patch where a name is
    # used" convention.
    monkeypatch.setattr("main.delete_file_from_storage", lambda key: storage_objects.pop(key, None))

    resp = client.post(
        "/auth/register",
        json={"email": "tool-cleanup@example.com", "password": "testpass123", "name": "Tool Cleanup"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    files = [
        ("files", ("a.pdf", io.BytesIO(_make_real_pdf("a")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_real_pdf("b")), "application/pdf")),
    ]
    merge_resp = client.post("/tools/merge-pdf", headers=headers, files=files)
    assert merge_resp.status_code == 200

    tool_file = db_session.query(ToolFile).first()
    assert tool_file is not None
    assert tool_file.storage_path in storage_objects  # really uploaded, not just a DB row

    del_resp = client.delete("/auth/me", headers=headers)
    assert del_resp.status_code == 204

    assert tool_file.storage_path not in storage_objects  # Storage object actually removed
    assert db_session.query(ToolFile).count() == 0


# ---------------------------------------------------------------------------
# H2 — rate limiting on /documents/{id}/chat and /documents/{id}/reprocess
# ---------------------------------------------------------------------------


def _make_document(db_session, user_id, **kwargs):
    doc = Document(
        user_id=user_id,
        filename="doc.pdf",
        storage_path="doc.pdf",
        status="processed",
        raw_text="Some real extracted text.",
        **kwargs,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_document_chat_is_rate_limited(client, db_session, monkeypatch):
    from rate_limit import limiter

    monkeypatch.setattr("main.answer_question", lambda raw_text, question: ("An answer.", False))

    resp = client.post(
        "/auth/register",
        json={"email": "chat-ratelimit@example.com", "password": "testpass123", "name": "Rate Limit"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    doc = _make_document(db_session, user_id)

    limiter.enabled = True
    try:
        statuses = [
            client.post(f"/documents/{doc.id}/chat", headers=headers, json={"question": "What is this?"}).status_code
            for _ in range(21)
        ]
    finally:
        limiter.enabled = False
        limiter.reset()

    assert 429 in statuses, "expected the 21st call within the same hour to be rate-limited"


def test_document_reprocess_is_rate_limited(client, db_session, monkeypatch):
    from rate_limit import limiter

    monkeypatch.setattr("main.classify_and_structure", lambda document, db: document)

    resp = client.post(
        "/auth/register",
        json={"email": "reprocess-ratelimit@example.com", "password": "testpass123", "name": "Rate Limit"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    doc = _make_document(db_session, user_id)

    limiter.enabled = True
    try:
        statuses = [
            client.post(f"/documents/{doc.id}/reprocess", headers=headers).status_code for _ in range(21)
        ]
    finally:
        limiter.enabled = False
        limiter.reset()

    assert 429 in statuses, "expected the 21st call within the same hour to be rate-limited"


# ---------------------------------------------------------------------------
# H3 — password change invalidates existing tokens
# ---------------------------------------------------------------------------


def test_changing_password_invalidates_the_old_token(client):
    resp = client.post(
        "/auth/register",
        json={"email": "token-version@example.com", "password": "oldpass123", "name": "Token Version"},
    )
    old_token = resp.json()["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    assert client.get("/auth/me", headers=old_headers).status_code == 200

    change_resp = client.post(
        "/auth/change-password",
        headers=old_headers,
        json={"current_password": "oldpass123", "new_password": "newpass456"},
    )
    assert change_resp.status_code == 204

    # The old token (issued before the password change) must now be rejected.
    assert client.get("/auth/me", headers=old_headers).status_code == 401

    # A fresh login with the new password gets a token that works normally.
    login_resp = client.post(
        "/auth/login", json={"email": "token-version@example.com", "password": "newpass456"}
    )
    new_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}
    assert client.get("/auth/me", headers=new_headers).status_code == 200


# ---------------------------------------------------------------------------
# H5 — JWT_SECRET_KEY startup validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [None, "", "too-short"])
def test_jwt_secret_key_validation_rejects_missing_or_weak_values(bad_value):
    with pytest.raises(RuntimeError):
        _validate_jwt_secret_key(bad_value)


def test_jwt_secret_key_validation_accepts_a_real_value():
    real_value = "a" * 43  # matches secrets.token_urlsafe(32)'s output length
    assert _validate_jwt_secret_key(real_value) == real_value


# ---------------------------------------------------------------------------
# sslmode=require on the Postgres connection string (non-local only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "db_url",
    [
        "postgresql://postgres:postgres@localhost:15432/documentos_test",
        "postgresql://postgres:postgres@127.0.0.1:15432/documentos_test",
    ],
)
def test_connect_args_omit_sslmode_for_local_connections(db_url):
    assert _connect_args_for(db_url) == {}


def test_connect_args_require_sslmode_for_non_local_connections():
    real_shaped_url = "postgresql://postgres:secret@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
    assert _connect_args_for(real_shaped_url) == {"sslmode": "require"}


# ---------------------------------------------------------------------------
# POST /auth/signature rate limiting
# ---------------------------------------------------------------------------


def test_signature_upload_is_rate_limited(client, monkeypatch):
    from rate_limit import limiter

    monkeypatch.setattr("signature_router.upload_file_to_storage", lambda data, key, content_type: key)
    monkeypatch.setattr("signature_router.delete_file_from_storage", lambda key: None)
    monkeypatch.setattr(
        "signature_router.create_signed_url",
        lambda key, expires_in: f"https://fake-signed-url.test/{key}",
    )

    resp = client.post(
        "/auth/register",
        json={"email": "signature-ratelimit@example.com", "password": "testpass123", "name": "Rate Limit"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def _make_png() -> bytes:
        from PIL import Image

        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    limiter.enabled = True
    try:
        statuses = [
            client.post(
                "/auth/signature",
                headers=headers,
                files={"file": ("signature.png", io.BytesIO(_make_png()), "image/png")},
            ).status_code
            for _ in range(21)
        ]
    finally:
        limiter.enabled = False
        limiter.reset()

    assert 429 in statuses, "expected the 21st call within the same hour to be rate-limited"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_viewing_downloading_and_deleting_a_document_writes_audit_log_rows(
    client, db_session, monkeypatch
):
    monkeypatch.setattr("main.create_signed_url", lambda key, expires_in: "https://fake-signed-url.test/x")
    monkeypatch.setattr("main.delete_file_from_storage", lambda key: None)

    resp = client.post(
        "/auth/register",
        json={"email": "audit-log@example.com", "password": "testpass123", "name": "Audit Log"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    doc = _make_document(db_session, user_id)

    assert client.get(f"/documents/{doc.id}", headers=headers).status_code == 200
    assert client.get(f"/documents/{doc.id}/download-url", headers=headers).status_code == 200
    assert client.delete(f"/documents/{doc.id}", headers=headers).status_code == 204

    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.resource_id == doc.id)
        .order_by(AuditLog.created_at.asc())
        .all()
    )
    actions = [log.action for log in logs]
    assert actions == ["view", "download", "delete"]
    for log in logs:
        assert log.resource_type == "document"
        assert str(log.user_id) == user_id
        assert log.user_email == "audit-log@example.com"


def test_audit_log_survives_account_deletion_with_user_id_nulled(client, db_session):
    resp = client.post(
        "/auth/register",
        json={"email": "audit-survives@example.com", "password": "testpass123", "name": "Audit Survives"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    assert client.delete("/auth/me", headers=headers).status_code == 204

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "delete_account", AuditLog.user_email == "audit-survives@example.com")
        .first()
    )
    assert log is not None
    assert log.user_id is None  # ON DELETE SET NULL, not CASCADE — the row itself survives
