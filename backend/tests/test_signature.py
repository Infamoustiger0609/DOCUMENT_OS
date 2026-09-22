"""Tests for the self-serve e-signature feature (Phase 31 — see CLAUDE.md's
E-signature section): POST/GET/DELETE /auth/signature, POST /documents/{id}/sign,
GET /documents/{id}/signed, and GET /signed-documents/{id}/download-url.

Supabase Storage is mocked (an in-memory dict, same "patch where a name is
used" convention as every other test file in this app — see conftest.py's
own storage_objects fixture for tools_common/editor_chat) via a
signature_router-local fixture, since this router imports its own copies of
upload/download/delete/create_signed_url from storage.py. The actual PDF
stamping (signature_tools.stamp_signature, real PyMuPDF + Pillow) is never
mocked — every test that signs a document exercises the real transformation
and verifies its real output.
"""

import io
import uuid

import pymupdf as fitz
import pytest
from PIL import Image

from models import Document


def _make_test_pdf(page_count: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 20), "Hello, this is a test PDF.")
    data = doc.tobytes()
    doc.close()
    return data


def _make_test_signature_png() -> bytes:
    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    for x in range(20, 280):
        img.putpixel((x, 50), (10, 10, 10, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def storage_fake(monkeypatch):
    """An in-memory dict standing in for Supabase Storage, keyed by storage
    path — local to this file since signature_router.py imports its own
    copies of these four functions from storage.py."""
    objects: dict = {}

    def fake_upload(data, key, content_type):
        objects[key] = data
        return key

    def fake_download(key):
        return objects[key]

    def fake_delete(key):
        objects.pop(key, None)

    monkeypatch.setattr("signature_router.upload_file_to_storage", fake_upload)
    monkeypatch.setattr("signature_router.download_file_from_storage", fake_download)
    monkeypatch.setattr("signature_router.delete_file_from_storage", fake_delete)
    monkeypatch.setattr(
        "signature_router.create_signed_url",
        lambda key, expires_in: f"https://fake-signed-url.test/{key}?expires_in={expires_in}",
    )
    return objects


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "signer@example.com", "password": "testpass123", "name": "Signer"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


def _seed_pdf_document(db_session, user_id, storage_path="fake/doc.pdf", filename="test.pdf"):
    doc = Document(
        user_id=user_id,
        filename=filename,
        storage_path=storage_path,
        status="processed",
        category="Agreement",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# /auth/signature
# ---------------------------------------------------------------------------


def test_auth_me_reports_no_signature_by_default(client, auth_headers):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.json()["has_signature"] is False


def test_save_signature_png_succeeds(client, auth_headers, storage_fake):
    png = _make_test_signature_png()
    resp = client.post(
        "/auth/signature",
        headers=auth_headers,
        files={"file": ("signature.png", png, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_signature"] is True
    assert data["download_url"] is not None

    me = client.get("/auth/me", headers=auth_headers).json()
    assert me["has_signature"] is True


def test_save_signature_rejects_wrong_extension(client, auth_headers, storage_fake):
    resp = client.post(
        "/auth/signature",
        headers=auth_headers,
        files={"file": ("signature.txt", b"not an image", "text/plain")},
    )
    assert resp.status_code == 400


def test_save_signature_rejects_content_mismatch(client, auth_headers, storage_fake):
    # A .png extension but the bytes aren't actually a PNG (magic-byte check).
    resp = client.post(
        "/auth/signature",
        headers=auth_headers,
        files={"file": ("signature.png", b"not really a png", "image/png")},
    )
    assert resp.status_code == 400


def test_save_signature_rejects_oversized_file(client, auth_headers, storage_fake):
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)
    resp = client.post(
        "/auth/signature",
        headers=auth_headers,
        files={"file": ("signature.png", oversized, "image/png")},
    )
    assert resp.status_code == 400


def test_get_signature_before_save_reports_false(client, auth_headers):
    resp = client.get("/auth/signature", headers=auth_headers)
    assert resp.json() == {"has_signature": False, "download_url": None, "expires_in": None}


def test_save_signature_replaces_previous_one(client, auth_headers, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})
    first_key = next(iter(storage_fake.keys()))

    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig2.png", png, "image/png")})
    # Same key (same extension, same user) — overwritten in place, not
    # duplicated, since there's only ever one saved signature per user.
    assert list(storage_fake.keys()) == [first_key]


def test_delete_signature_clears_it(client, auth_headers, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})
    assert len(storage_fake) == 1

    resp = client.delete("/auth/signature", headers=auth_headers)
    assert resp.status_code == 204
    assert len(storage_fake) == 0

    me = client.get("/auth/me", headers=auth_headers).json()
    assert me["has_signature"] is False


# ---------------------------------------------------------------------------
# POST /documents/{id}/sign
# ---------------------------------------------------------------------------


def test_sign_requires_a_saved_signature_first(client, db_session, auth_headers, current_user_id, storage_fake):
    doc = _seed_pdf_document(db_session, current_user_id)
    resp = client.post(f"/documents/{doc.id}/sign", headers=auth_headers)
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


def test_sign_rejects_non_pdf_document(client, db_session, auth_headers, current_user_id, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})

    doc = _seed_pdf_document(db_session, current_user_id, storage_path="fake/scan.png", filename="scan.png")
    resp = client.post(f"/documents/{doc.id}/sign", headers=auth_headers)
    assert resp.status_code == 400
    assert "PDF" in resp.json()["detail"]


def test_sign_produces_a_real_stamped_pdf(client, db_session, auth_headers, current_user_id, storage_fake):
    signature_png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", signature_png, "image/png")})

    pdf_bytes = _make_test_pdf(page_count=2)
    doc = _seed_pdf_document(db_session, current_user_id, storage_path="fake/original.pdf", filename="contract.pdf")
    storage_fake["fake/original.pdf"] = pdf_bytes

    resp = client.post(f"/documents/{doc.id}/sign", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["document_id"] == str(doc.id)
    assert data["filename"] == "signed_contract.pdf"

    # Find the produced signed PDF in the fake store and verify it for real.
    signed_keys = [k for k in storage_fake if k.startswith("signed-documents/")]
    assert len(signed_keys) == 1
    signed_bytes = storage_fake[signed_keys[0]]

    signed_doc = fitz.open(stream=signed_bytes, filetype="pdf")
    try:
        assert signed_doc.page_count == 2  # page count unchanged
        last_page = signed_doc[-1]
        assert len(last_page.get_images()) == 1  # the signature image was actually embedded
    finally:
        signed_doc.close()

    # The original document's own storage bytes are untouched.
    assert storage_fake["fake/original.pdf"] == pdf_bytes


def test_sign_isolated_from_other_users_document(client, db_session, auth_headers, current_user_id, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})

    other_token = client.post(
        "/auth/register", json={"email": "other-signer@example.com", "password": "testpass123", "name": "Other"}
    ).json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]
    other_doc = _seed_pdf_document(db_session, other_user_id, storage_path="fake/other.pdf")

    resp = client.post(f"/documents/{other_doc.id}/sign", headers=auth_headers)
    assert resp.status_code == 404


def test_sign_requires_auth(client):
    resp = client.post(f"/documents/{uuid.uuid4()}/sign")
    assert resp.status_code == 401


def test_list_signed_versions_and_download_url(client, db_session, auth_headers, current_user_id, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})

    pdf_bytes = _make_test_pdf()
    doc = _seed_pdf_document(db_session, current_user_id, storage_path="fake/original.pdf")
    storage_fake["fake/original.pdf"] = pdf_bytes

    client.post(f"/documents/{doc.id}/sign", headers=auth_headers)

    listing = client.get(f"/documents/{doc.id}/signed", headers=auth_headers)
    assert listing.status_code == 200
    versions = listing.json()
    assert len(versions) == 1
    signed_id = versions[0]["id"]

    download = client.get(f"/signed-documents/{signed_id}/download-url", headers=auth_headers)
    assert download.status_code == 200
    assert download.json()["url"].startswith("https://fake-signed-url.test/")


def test_download_url_isolated_from_other_users(client, db_session, auth_headers, current_user_id, storage_fake):
    png = _make_test_signature_png()
    client.post("/auth/signature", headers=auth_headers, files={"file": ("sig.png", png, "image/png")})
    pdf_bytes = _make_test_pdf()
    doc = _seed_pdf_document(db_session, current_user_id, storage_path="fake/original.pdf")
    storage_fake["fake/original.pdf"] = pdf_bytes
    signed_id = client.post(f"/documents/{doc.id}/sign", headers=auth_headers).json()["id"]

    other_token = client.post(
        "/auth/register", json={"email": "other-viewer@example.com", "password": "testpass123", "name": "Other"}
    ).json()["access_token"]
    resp = client.get(
        f"/signed-documents/{signed_id}/download-url", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 404
