import io

import pytest

FAKE_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "uploader@example.com", "password": "testpass123", "name": "Uploader"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_returns_immediately_then_processes_in_the_background(client, auth_headers, monkeypatch):
    """Full extract -> classify -> structure pipeline, with Storage and Groq
    mocked out (see CLAUDE.md's Testing section for why: no real API cost, no
    dependency on Supabase Storage/Tesseract being reachable in CI).

    See CLAUDE.md's Background processing section: POST /documents/upload
    returns before the pipeline runs, so the response body itself is just the
    "uploaded" state — the final state only shows up on a later
    GET /documents/{id}, exactly like a real polling client would see it.
    TestClient runs a request's background tasks to completion before
    client.post() returns control here (verified separately against a live
    uvicorn server too — a real client's response arrives immediately while
    the task keeps running), so the GET right below doesn't need a manual wait."""
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr(
        "processing.extract_text",
        lambda file_bytes, extension: "Invoice #123 from Acme Corp, due 2026-12-01, total $500.",
    )
    monkeypatch.setattr(
        "processing.classify_text",
        lambda raw_text: ("Invoice", "Has an invoice number, vendor, and due date."),
    )
    monkeypatch.setattr(
        "processing.extract_structured_data",
        lambda category, raw_text: {
            "vendor_name": "Acme Corp",
            "invoice_number": "123",
            "invoice_date": "2026-11-01",
            "due_date": "2026-12-01",
            "amount": 500,
            "gst_number": None,
        },
    )

    files = {"file": ("invoice.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    upload_resp = client.post("/documents/upload", headers=auth_headers, files=files)

    assert upload_resp.status_code == 200
    upload_data = upload_resp.json()
    assert upload_data["status"] == "uploaded"
    assert upload_data["category"] is None
    assert upload_data["extracted_json"] is None

    detail_resp = client.get(f"/documents/{upload_data['id']}", headers=auth_headers)
    data = detail_resp.json()
    assert data["status"] == "processed"
    assert data["category"] == "Invoice"
    assert data["extracted_json"]["vendor_name"] == "Acme Corp"
    assert data["deadline_date"] == "2026-12-01"
    assert data["classification_reasoning"] == "Has an invoice number, vendor, and due date."


def test_upload_classification_failure_sanitizes_error_message(client, auth_headers, monkeypatch):
    """See the Security section in CLAUDE.md: error_message must never contain
    raw exception text, since it's visible to any authenticated user via
    GET /documents, not just the uploader."""
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "some raw text")

    def boom(raw_text):
        raise RuntimeError("groq api key abc123 rejected the request")

    monkeypatch.setattr("processing.classify_text", boom)

    files = {"file": ("invoice.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    upload_resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert upload_resp.status_code == 200  # pipeline failures are recorded, not raised as a 500

    data = client.get(f"/documents/{upload_resp.json()['id']}", headers=auth_headers).json()
    assert data["status"] == "classification_failed"
    assert "abc123" not in (data["error_message"] or "")
    assert data["error_message"] == (
        "Classification failed. Try reprocessing this document, or contact support if it "
        "keeps failing."
    )


def test_upload_skips_structured_extraction_for_bank_statement(client, auth_headers, monkeypatch):
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "Statement for account ending 1234.")
    monkeypatch.setattr(
        "processing.classify_text", lambda raw_text: ("Bank Statement", "Looks like an account statement.")
    )

    files = {"file": ("statement.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    upload_resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert upload_resp.status_code == 200

    data = client.get(f"/documents/{upload_resp.json()['id']}", headers=auth_headers).json()
    assert data["status"] == "processed"
    assert data["category"] == "Bank Statement"
    assert data["extracted_json"] is None


def test_upload_response_never_reflects_background_processing_yet(client, auth_headers, monkeypatch):
    """Direct regression test for the core Phase 18 change — even though the
    mocked pipeline below would succeed near-instantly, the upload response
    itself must never be anything but the immediate "uploaded" state, since a
    real pipeline run (OCR + up to two Groq calls) can take much longer than
    this test's mocks do, and the whole point is not to hold the HTTP request
    open for it."""
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "Invoice text")
    monkeypatch.setattr("processing.classify_text", lambda raw_text: ("Invoice", "reasoning"))
    monkeypatch.setattr(
        "processing.extract_structured_data",
        lambda category, raw_text: {"vendor_name": "Acme"},
    )

    files = {"file": ("invoice.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    resp = client.post("/documents/upload", headers=auth_headers, files=files)

    data = resp.json()
    assert data["status"] == "uploaded"
    assert data["category"] is None
    assert data["classification_reasoning"] is None
    assert data["deadline_date"] is None


def test_upload_rejects_content_extension_mismatch(client, auth_headers):
    """See file_validation.py / the Security section in CLAUDE.md."""
    files = {"file": ("fake.pdf", io.BytesIO(b"this is plain text, not a pdf"), "application/pdf")}
    resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert resp.status_code == 400
    assert "doesn't match" in resp.json()["detail"]


def test_upload_rejects_empty_file(client, auth_headers):
    files = {"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
    resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_upload_rejects_unsupported_extension(client, auth_headers):
    files = {"file": ("archive.zip", io.BytesIO(b"PK\x03\x04"), "application/zip")}
    resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_upload_requires_auth(client):
    files = {"file": ("invoice.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 401
