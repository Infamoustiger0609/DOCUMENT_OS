"""Tests for the /tools/* document-editing endpoints (see CLAUDE.md's Document
tools section). merge-pdf/split-pdf (pikepdf) and convert-image/resize-image/
pdf-to-docx (Pillow/pdf2docx) are pure-Python, so these run against the real
transformation logic. compress-pdf/docx-to-pdf/ocr-pdf shell out to Ghostscript/
LibreOffice/Tesseract — real system binaries this CI environment doesn't
install (same reasoning as extraction.py's Tesseract dependency never being
exercised for real in test_upload_pipeline.py) — so those three mock the
transform function itself via monkeypatch, and only assert the endpoint's
plumbing around it (validation, storage, response shape).

Storage is mocked throughout (no real Supabase Storage call in this suite) by
patching tools_common's imported upload_file_to_storage/delete_file_from_storage
and tools_router's imported create_signed_url — patching where each name is
*used*, per this project's own monkeypatch convention (see CLAUDE.md's Testing
section)."""

import io
import uuid

import pikepdf
import pymupdf as fitz  # PyMuPDF's `fitz` import name is deprecated in favor of `pymupdf`
import pytest
from docx import Document as DocxDocument
from PIL import Image

from models import ToolFile


def _make_pdf(text: str, page_count: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} (page {i + 1})")
    data = doc.tobytes()
    doc.close()
    return data


def _make_png(size=(64, 64), color=(200, 50, 50)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "tools-tester@example.com", "password": "testpass123", "name": "Tools Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# storage_objects fixture lives in conftest.py (shared with test_editor_chat.py).


def _get_tool_file(db_session, tool_file_id: str) -> ToolFile:
    return db_session.query(ToolFile).filter(ToolFile.id == uuid.UUID(tool_file_id)).one()


# ---------------------------------------------------------------------------
# merge-pdf
# ---------------------------------------------------------------------------


def test_merge_pdf_success(client, auth_headers, storage_objects, db_session):
    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("Document A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("Document B")), "application/pdf")),
    ]
    resp = client.post("/tools/merge-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_name"] == "merge-pdf"
    assert data["output_filename"] == "merged.pdf"
    assert data["original_filename"] == "a.pdf, b.pdf"

    row = _get_tool_file(db_session, data["id"])
    with pikepdf.open(io.BytesIO(storage_objects[row.storage_path])) as merged:
        assert len(merged.pages) == 2


def test_merge_pdf_requires_two_files(client, auth_headers):
    files = [("files", ("a.pdf", io.BytesIO(_make_pdf("Solo")), "application/pdf"))]
    resp = client.post("/tools/merge-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_merge_pdf_rejects_content_extension_mismatch(client, auth_headers):
    files = [
        ("files", ("a.pdf", io.BytesIO(b"not a real pdf"), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    resp = client.post("/tools/merge-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_merge_pdf_requires_auth(client):
    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    resp = client.post("/tools/merge-pdf", files=files)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# split-pdf
# ---------------------------------------------------------------------------


def test_split_pdf_default_is_one_file_per_page(client, auth_headers, storage_objects, db_session):
    files = {"file": ("doc.pdf", io.BytesIO(_make_pdf("Doc", page_count=3)), "application/pdf")}
    resp = client.post("/tools/split-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 200
    parts = resp.json()
    assert [p["output_filename"] for p in parts] == ["page_1.pdf", "page_2.pdf", "page_3.pdf"]
    for part in parts:
        row = _get_tool_file(db_session, part["id"])
        with pikepdf.open(io.BytesIO(storage_objects[row.storage_path])) as pdf:
            assert len(pdf.pages) == 1


def test_split_pdf_with_ranges(client, auth_headers, storage_objects):
    files = {"file": ("doc.pdf", io.BytesIO(_make_pdf("Doc", page_count=3)), "application/pdf")}
    resp = client.post("/tools/split-pdf", headers=auth_headers, files=files, data={"ranges": "1-2,3"})
    assert resp.status_code == 200
    parts = resp.json()
    assert [p["output_filename"] for p in parts] == ["pages_1-2.pdf", "page_3.pdf"]


def test_split_pdf_rejects_out_of_range(client, auth_headers):
    files = {"file": ("doc.pdf", io.BytesIO(_make_pdf("Doc", page_count=2)), "application/pdf")}
    resp = client.post("/tools/split-pdf", headers=auth_headers, files=files, data={"ranges": "5-9"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# compress-pdf (Ghostscript mocked — see module docstring)
# ---------------------------------------------------------------------------


def test_compress_pdf_success(client, auth_headers, storage_objects, monkeypatch):
    monkeypatch.setattr("tools_router.compress_pdf", lambda data, quality: b"%PDF-1.4 compressed-fake")
    files = {"file": ("big.pdf", io.BytesIO(_make_pdf("Big")), "application/pdf")}
    resp = client.post("/tools/compress-pdf", headers=auth_headers, files=files, data={"quality": "low"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["output_filename"] == "compressed_big.pdf"
    assert data["tool_name"] == "compress-pdf"


def test_compress_pdf_rejects_non_pdf_extension(client, auth_headers):
    files = {"file": ("big.txt", io.BytesIO(b"hello world"), "text/plain")}
    resp = client.post("/tools/compress-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_compress_pdf_reports_ghostscript_failure_as_502(client, auth_headers, monkeypatch):
    def boom(data, quality):
        raise RuntimeError("Ghostscript failed (exit 1): some internal detail")

    monkeypatch.setattr("tools_router.compress_pdf", boom)
    files = {"file": ("big.pdf", io.BytesIO(_make_pdf("Big")), "application/pdf")}
    resp = client.post("/tools/compress-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 502
    # never leak the raw exception text to the client (same policy as
    # processing.py's error_message sanitization — see CLAUDE.md's Security section)
    assert "internal detail" not in resp.json()["detail"]


# ---------------------------------------------------------------------------
# pdf-to-docx (pure Python — pdf2docx/PyMuPDF, no external binary)
# ---------------------------------------------------------------------------


def test_pdf_to_docx_success(client, auth_headers, storage_objects, db_session):
    files = {"file": ("doc.pdf", io.BytesIO(_make_pdf("Convert me")), "application/pdf")}
    resp = client.post("/tools/pdf-to-docx", headers=auth_headers, files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["output_filename"] == "doc.docx"

    row = _get_tool_file(db_session, data["id"])
    docx_doc = DocxDocument(io.BytesIO(storage_objects[row.storage_path]))
    full_text = "\n".join(p.text for p in docx_doc.paragraphs)
    assert "Convert me" in full_text


# ---------------------------------------------------------------------------
# docx-to-pdf (LibreOffice mocked — see module docstring)
# ---------------------------------------------------------------------------


def test_docx_to_pdf_success(client, auth_headers, storage_objects, monkeypatch):
    monkeypatch.setattr("tools_router.docx_to_pdf", lambda data: b"%PDF-1.4 fake-converted")
    files = {
        "file": (
            "sample.docx",
            io.BytesIO(b"PK\x03\x04fake-zip-content"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    resp = client.post("/tools/docx-to-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 200
    assert resp.json()["output_filename"] == "sample.pdf"


def test_docx_to_pdf_rejects_content_extension_mismatch(client, auth_headers):
    files = {"file": ("sample.docx", io.BytesIO(b"not actually a zip"), "application/octet-stream")}
    resp = client.post("/tools/docx-to-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# convert-image / resize-image (pure Python — Pillow, no external binary)
# ---------------------------------------------------------------------------


def test_convert_image_success(client, auth_headers, storage_objects, db_session):
    files = {"file": ("photo.png", io.BytesIO(_make_png()), "image/png")}
    resp = client.post("/tools/convert-image", headers=auth_headers, files=files, data={"target_format": "jpg"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["output_filename"] == "photo.jpg"
    assert data["mime_type"] == "image/jpeg"

    row = _get_tool_file(db_session, data["id"])
    converted = Image.open(io.BytesIO(storage_objects[row.storage_path]))
    assert converted.format == "JPEG"


def test_convert_image_rejects_unsupported_target(client, auth_headers):
    files = {"file": ("photo.png", io.BytesIO(_make_png()), "image/png")}
    resp = client.post("/tools/convert-image", headers=auth_headers, files=files, data={"target_format": "bmp"})
    assert resp.status_code == 422  # Literal["jpg","png","tiff"] rejects it before the handler runs


def test_resize_image_by_width_preserves_aspect_ratio(client, auth_headers, storage_objects, db_session):
    files = {"file": ("photo.png", io.BytesIO(_make_png(size=(800, 600))), "image/png")}
    resp = client.post("/tools/resize-image", headers=auth_headers, files=files, data={"width": "400"})
    assert resp.status_code == 200
    row = _get_tool_file(db_session, resp.json()["id"])
    resized = Image.open(io.BytesIO(storage_objects[row.storage_path]))
    assert resized.size == (400, 300)


def test_resize_image_requires_at_least_one_parameter(client, auth_headers):
    files = {"file": ("photo.png", io.BytesIO(_make_png()), "image/png")}
    resp = client.post("/tools/resize-image", headers=auth_headers, files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ocr-pdf (Tesseract/Ghostscript mocked — see module docstring)
# ---------------------------------------------------------------------------


def test_ocr_pdf_success(client, auth_headers, storage_objects, monkeypatch):
    monkeypatch.setattr("tools_router.ocr_pdf", lambda data, force_ocr: b"%PDF-1.4 searchable-fake")
    files = {"file": ("scan.pdf", io.BytesIO(_make_pdf("Scan")), "application/pdf")}
    resp = client.post("/tools/ocr-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 200
    assert resp.json()["output_filename"] == "searchable_scan.pdf"


def test_ocr_pdf_rejects_non_pdf(client, auth_headers):
    files = {"file": ("scan.jpg", io.BytesIO(b"\xff\xd8\xff fake jpeg"), "image/jpeg")}
    resp = client.post("/tools/ocr-pdf", headers=auth_headers, files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# download-url + per-user isolation
# ---------------------------------------------------------------------------


def test_download_url_returns_signed_url(client, auth_headers, storage_objects):
    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    tool_file_id = client.post("/tools/merge-pdf", headers=auth_headers, files=files).json()["id"]

    resp = client.get(f"/tools/{tool_file_id}/download-url", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"].startswith("https://fake-signed-url.test/")
    assert data["expires_in"] == 300


def test_download_url_404s_for_another_users_file(client, auth_headers, storage_objects):
    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    tool_file_id = client.post("/tools/merge-pdf", headers=auth_headers, files=files).json()["id"]

    other_token = client.post(
        "/auth/register",
        json={"email": "someone-else@example.com", "password": "testpass123", "name": "Someone Else"},
    ).json()["access_token"]

    resp = client.get(
        f"/tools/{tool_file_id}/download-url", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 404


def test_download_url_requires_auth(client):
    resp = client.get(f"/tools/{uuid.uuid4()}/download-url")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# retention sweep
# ---------------------------------------------------------------------------


def test_expired_tool_file_is_swept_on_next_request(client, auth_headers, storage_objects, db_session):
    """See tools_common.cleanup_expired_tool_files — runs as a dependency on
    every /tools/* request. Seeds an already-expired row directly (bypassing
    the API, like test_caching.py's cache-hit test does), then confirms the
    next /tools/* call deletes both the DB row and its Storage object."""
    from datetime import datetime, timedelta, timezone

    me = client.get("/auth/me", headers=auth_headers).json()

    storage_objects["tool-outputs/some-user/stale.pdf"] = b"stale bytes"
    stale = ToolFile(
        user_id=uuid.UUID(me["id"]),
        tool_name="merge-pdf",
        original_filename="a.pdf, b.pdf",
        output_filename="merged.pdf",
        storage_path="tool-outputs/some-user/stale.pdf",
        mime_type="application/pdf",
        size_bytes=11,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(stale)
    db_session.commit()
    stale_id = stale.id

    # Any /tools/* request runs the cleanup dependency first.
    files = {"file": ("photo.png", io.BytesIO(_make_png()), "image/png")}
    resp = client.post("/tools/resize-image", headers=auth_headers, files=files, data={"width": "10"})
    assert resp.status_code == 200

    assert db_session.query(ToolFile).filter(ToolFile.id == stale_id).first() is None
    assert "tool-outputs/some-user/stale.pdf" not in storage_objects
