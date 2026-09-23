"""Tests for document generation from a learned template (Phase 32 — see
CLAUDE.md's Document generation section): POST /templates/learn-from-sample,
GET/DELETE /templates(/{id}), and POST /templates/{id}/generate.

Groq is always mocked (`template_learning.client.chat.completions.create`
for schema learning/instruction parsing, `templates_router.classify_text`
for classification — same "patch where a name is used" convention as every
other test file in this app). Storage is mocked at `templates_router`'s own
imported name. Text extraction is NEVER mocked — every sample PDF here is a
real, minimal PDF built with PyMuPDF containing real embedded text, so
`extract_text()` runs for real via its normal PyMuPDF text-layer path (no
OCR needed, no Storage/Tesseract dependency in this test).

The actual PDF *generation* (template_generation.render_document, real
ReportLab) is never mocked either — test_generate_produces_a_real_pdf_with_
new_values renders a real PDF and re-opens it with PyMuPDF to assert on its
actual text content directly.
"""

import io
import json
import re
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from models import Template, User


def _make_invoice_pdf(vendor: str = "Sample Vendor Ltd", invoice_number: str = "SAMPLE-001") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), f"TAX INVOICE\nVendor: {vendor}\nInvoice Number: {invoice_number}")
    page.insert_text((50, 100), "Description: Widgets   Qty: 10   Rate: 50.00   Amount: 500.00")
    page.insert_text((50, 130), "Total Due: 500.00")
    data = doc.tobytes()
    doc.close()
    return data


INVOICE_SCHEMA = {
    "document_type": "Invoice",
    "sections": [
        {
            "id": "header",
            "title": "Header",
            "type": "fields",
            "fields": [
                {"key": "vendor_name", "label": "Vendor Name", "type": "string"},
                {"key": "invoice_number", "label": "Invoice Number", "type": "string"},
                {"key": "due_date", "label": "Due Date", "type": "date"},
            ],
        },
        {
            "id": "line_items",
            "title": "Line Items",
            "type": "table",
            "columns": [
                {"key": "description", "label": "Description", "type": "string"},
                {"key": "quantity", "label": "Qty", "type": "number"},
                {"key": "rate", "label": "Rate", "type": "number"},
                {"key": "amount", "label": "Amount", "type": "number"},
            ],
        },
        {
            "id": "totals",
            "title": "Totals",
            "type": "fields",
            "fields": [{"key": "total", "label": "Total", "type": "number"}],
        },
    ],
}


def _fake_groq_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture()
def storage_fake(monkeypatch):
    objects: dict = {}

    def fake_upload(data, key, content_type):
        objects[key] = data
        return key

    monkeypatch.setattr("templates_router.upload_file_to_storage", fake_upload)
    return objects


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "templates-tester@example.com", "password": "testpass123", "name": "Templates Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


def _mock_classification(monkeypatch, category: str, reasoning: str = "reasoning"):
    monkeypatch.setattr("templates_router.classify_text", lambda raw_text: (category, reasoning))


def _mock_schema_learning(monkeypatch, schema: dict):
    monkeypatch.setattr(
        "template_learning.client.chat.completions.create",
        lambda **kwargs: _fake_groq_response(json.dumps(schema)),
    )


# ---------------------------------------------------------------------------
# POST /templates/learn-from-sample
# ---------------------------------------------------------------------------


def test_learn_from_sample_requires_auth(client):
    resp = client.post("/templates/learn-from-sample", files={"file": ("sample.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert resp.status_code == 401


def test_learn_from_sample_happy_path(client, auth_headers, storage_fake, monkeypatch):
    _mock_classification(monkeypatch, "Invoice")
    _mock_schema_learning(monkeypatch, INVOICE_SCHEMA)

    pdf_bytes = _make_invoice_pdf()
    resp = client.post(
        "/templates/learn-from-sample",
        headers=auth_headers,
        files={"file": ("sample_invoice.pdf", pdf_bytes, "application/pdf")},
        data={"name": "My Invoice Template"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "My Invoice Template"
    assert data["category"] == "Invoice"
    assert data["created_from_document_id"] is not None
    assert [s["id"] for s in data["field_schema"]["sections"]] == ["header", "line_items", "totals"]

    # The source sample is a real, ordinary document in the user's registry.
    doc_id = data["created_from_document_id"]
    doc_resp = client.get(f"/documents/{doc_id}", headers=auth_headers)
    assert doc_resp.status_code == 200
    assert doc_resp.json()["category"] == "Invoice"
    assert doc_resp.json()["status"] == "processed"


def test_learn_from_sample_defaults_name_when_not_given(client, auth_headers, storage_fake, monkeypatch):
    _mock_classification(monkeypatch, "Invoice")
    _mock_schema_learning(monkeypatch, INVOICE_SCHEMA)

    resp = client.post(
        "/templates/learn-from-sample",
        headers=auth_headers,
        files={"file": ("sample_invoice.pdf", _make_invoice_pdf(), "application/pdf")},
    )
    assert resp.status_code == 200
    assert "Invoice template" in resp.json()["name"]


def test_learn_from_sample_rejects_unsupported_category(client, auth_headers, storage_fake, monkeypatch):
    _mock_classification(monkeypatch, "Bank Statement")

    resp = client.post(
        "/templates/learn-from-sample",
        headers=auth_headers,
        files={"file": ("sample_statement.pdf", _make_invoice_pdf(), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Bank Statement" in resp.json()["detail"]

    # The sample document itself is still saved (a real, legitimate upload) —
    # only template creation was refused. It's a template-learning sample
    # (source="template_sample"), so it's excluded from the main registry
    # and only shows up via GET /documents/templates — see CLAUDE.md's
    # "Document source separation" section.
    assert client.get("/documents", headers=auth_headers).json() == []
    sample_docs = client.get("/documents/templates", headers=auth_headers).json()
    assert len(sample_docs) == 1
    assert sample_docs[0]["category"] == "Bank Statement"


def test_learn_from_sample_rejects_content_mismatch(client, auth_headers, storage_fake):
    resp = client.post(
        "/templates/learn-from-sample",
        headers=auth_headers,
        files={"file": ("sample.pdf", b"not a real pdf", "application/pdf")},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET/DELETE /templates(/{id})
# ---------------------------------------------------------------------------


def _seed_template(db_session, user_id, category="Invoice", schema=None):
    template = Template(
        user_id=user_id, name="Test Template", category=category, field_schema=schema or INVOICE_SCHEMA
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    return template


def test_list_templates_scoped_to_own_user(client, db_session, auth_headers, current_user_id):
    _seed_template(db_session, current_user_id)

    other_token = client.post(
        "/auth/register", json={"email": "other-templates@example.com", "password": "testpass123", "name": "Other"}
    ).json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]
    _seed_template(db_session, other_user_id)

    resp = client.get("/templates", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_and_delete_template_isolated_from_other_users(client, db_session, auth_headers, current_user_id):
    template = _seed_template(db_session, current_user_id)

    other_token = client.post(
        "/auth/register", json={"email": "other-templates2@example.com", "password": "testpass123", "name": "Other"}
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    assert client.get(f"/templates/{template.id}", headers=other_headers).status_code == 404
    assert client.delete(f"/templates/{template.id}", headers=other_headers).status_code == 404

    assert client.get(f"/templates/{template.id}", headers=auth_headers).status_code == 200
    assert client.delete(f"/templates/{template.id}", headers=auth_headers).status_code == 204
    assert client.get(f"/templates/{template.id}", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# POST /templates/{id}/generate
# ---------------------------------------------------------------------------


def test_generate_requires_values_or_instruction(client, db_session, auth_headers, current_user_id):
    template = _seed_template(db_session, current_user_id)
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={})
    assert resp.status_code == 400


def test_generate_isolated_from_other_users_template(client, db_session, auth_headers, current_user_id):
    other_token = client.post(
        "/auth/register", json={"email": "other-templates3@example.com", "password": "testpass123", "name": "Other"}
    ).json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]
    template = _seed_template(db_session, other_user_id)

    resp = client.post(
        f"/templates/{template.id}/generate",
        headers=auth_headers,
        json={"values": {"header": {"vendor_name": "X"}}},
    )
    assert resp.status_code == 404


def test_generate_produces_a_real_pdf_with_new_values(client, db_session, auth_headers, current_user_id, storage_fake):
    template = _seed_template(db_session, current_user_id)

    values = {
        "header": {"vendor_name": "Northwind Traders", "invoice_number": "INV-9999", "due_date": "2026-11-15"},
        "line_items": [
            {"description": "Consulting hours", "quantity": 8, "rate": 125.5, "amount": 1004.0},
        ],
        "totals": {"total": 1004.0},
    }
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={"values": values})
    assert resp.status_code == 200
    data = resp.json()
    assert data["category"] == "Invoice"
    assert data["status"] == "processed"
    assert data["deadline_date"] == "2026-11-15"
    assert data["generated_from_template_id"] == str(template.id)

    # extracted_json is self-describing (schema + values), not the fixed
    # flat-key shape structured_extraction.py uses.
    assert data["extracted_json"]["_generated"] is True
    assert data["extracted_json"]["_values"]["header"]["vendor_name"] == "Northwind Traders"

    # The actual rendered PDF (never mocked) contains the NEW values...
    generated_key = next(iter(storage_fake.keys()))
    pdf_bytes = storage_fake[generated_key]
    rendered = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = "".join(page.get_text() for page in rendered)
    finally:
        rendered.close()
    assert "Northwind Traders" in text
    assert "INV-9999" in text
    assert "Consulting hours" in text
    # ...and NOT the seeded template's own sample data (there is none here —
    # this template was seeded directly with no created_from sample text —
    # but critically the vendor from a DIFFERENT, unrelated sample used
    # elsewhere in this file never appears).
    assert "Sample Vendor Ltd" not in text
    assert "SAMPLE-001" not in text

    # And it's a genuinely valid, real document in the registry now.
    doc_resp = client.get(f"/documents/{data['id']}", headers=auth_headers)
    assert doc_resp.status_code == 200
    assert "Northwind Traders" in doc_resp.json()["raw_text"]


def test_generate_from_instruction_parses_via_groq(client, db_session, auth_headers, current_user_id, storage_fake, monkeypatch):
    template = _seed_template(db_session, current_user_id)

    parsed_values = {
        "header": {"vendor_name": "Acme Corp", "invoice_number": "INV-4242", "due_date": "2026-12-01"},
        "line_items": [{"description": "Widgets", "quantity": 5, "rate": 100.0, "amount": 500.0}],
        "totals": {"total": 500.0},
    }
    monkeypatch.setattr(
        "template_learning.client.chat.completions.create",
        lambda **kwargs: _fake_groq_response(json.dumps(parsed_values)),
    )

    resp = client.post(
        f"/templates/{template.id}/generate",
        headers=auth_headers,
        json={"instruction": "Generate an invoice for Acme Corp, 5 widgets at 100 each, due Dec 1 2026."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["extracted_json"]["_values"]["header"]["vendor_name"] == "Acme Corp"
    assert data["deadline_date"] == "2026-12-01"


def test_generate_embeds_users_saved_signature(client, db_session, auth_headers, current_user_id, storage_fake, monkeypatch):
    """Phase 32's PDF redesign added a signature block that embeds the
    user's own Phase 31 saved signature image (users.signature_storage_path)
    when one exists. templates_router fetches it via its own imported
    download_file_from_storage (patched here, same "patch where a name is
    used" convention as storage_fake above patches upload_file_to_storage)."""
    import io

    from PIL import Image as PILImage

    user = db_session.query(User).filter(User.id == current_user_id).first()
    user.signature_storage_path = f"signatures/{current_user_id}/signature.png"
    db_session.add(user)
    db_session.commit()

    sig_img = PILImage.new("RGBA", (200, 60), (0, 0, 0, 0))
    for x in range(10, 190):
        sig_img.putpixel((x, 30), (10, 10, 80, 255))
    buf = io.BytesIO()
    sig_img.save(buf, format="PNG")
    signature_bytes = buf.getvalue()

    monkeypatch.setattr("templates_router.download_file_from_storage", lambda path: signature_bytes)

    template = _seed_template(db_session, current_user_id)
    values = {
        "header": {"vendor_name": "Acme", "invoice_number": "INV-1", "due_date": "2026-11-15"},
        "line_items": [{"description": "Widgets", "quantity": 1, "rate": 10.0, "amount": 10.0}],
        "totals": {"total": 10.0},
    }
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={"values": values})
    assert resp.status_code == 200

    generated_key = next(iter(storage_fake.keys()))
    rendered = fitz.open(stream=storage_fake[generated_key], filetype="pdf")
    try:
        last_page = rendered[-1]
        assert len(last_page.get_images()) >= 1
    finally:
        rendered.close()


def test_generate_ignores_missing_signature_fetch_failure(client, db_session, auth_headers, current_user_id, storage_fake, monkeypatch):
    """A saved signature that can't be fetched right now (e.g. a transient
    Storage error) must not block generation at all — best-effort, falls
    back to blank signature space, same as a user with no saved signature."""
    user = db_session.query(User).filter(User.id == current_user_id).first()
    user.signature_storage_path = f"signatures/{current_user_id}/signature.png"
    db_session.add(user)
    db_session.commit()

    def _raise(path):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("templates_router.download_file_from_storage", _raise)

    template = _seed_template(db_session, current_user_id)
    values = {
        "header": {"vendor_name": "Acme", "invoice_number": "INV-2", "due_date": "2026-11-15"},
        "line_items": [{"description": "Widgets", "quantity": 1, "rate": 10.0, "amount": 10.0}],
        "totals": {"total": 10.0},
    }
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={"values": values})
    assert resp.status_code == 200


def test_generate_uses_a_human_readable_filename_not_a_raw_uuid(client, db_session, auth_headers, current_user_id, storage_fake):
    """Regression test for a real bug: a generated document's user-facing
    filename was showing a UUID-embedded string (the internal Storage key's
    own basename), e.g. "440593cc-...-4349-8420-...  _Invoice_a34cdcec.pdf".
    A UUID must only ever appear in storage_path, never in filename."""
    template = _seed_template(db_session, current_user_id)
    values = {
        "header": {"vendor_name": "Northwind Traders", "invoice_number": "INV-9999", "due_date": "2026-11-15"},
        "line_items": [{"description": "Consulting hours", "quantity": 8, "rate": 125.5, "amount": 1004.0}],
        "totals": {"total": 1004.0},
    }
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={"values": values})
    assert resp.status_code == 200
    data = resp.json()

    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
    assert not uuid_pattern.search(data["filename"])
    assert data["filename"].startswith("Invoice - Northwind Traders")
    assert data["filename"].endswith(".pdf")

    # The UUID still lives in storage_path (the internal Storage key) — it's
    # just never allowed to leak into filename, which is what a user actually
    # sees and what a real download gets saved as.
    assert uuid_pattern.search(data["storage_path"])
    assert data["storage_path"] != data["filename"]


def test_generate_ignores_extra_unknown_keys_in_values(client, db_session, auth_headers, current_user_id, storage_fake):
    """normalize_values() forces the payload into exactly the schema's own
    keys — a stray/unexpected key in the request must not leak into the
    rendered document or stored extracted_json."""
    template = _seed_template(db_session, current_user_id)
    values = {
        "header": {"vendor_name": "Acme", "totally_made_up_field": "should be dropped"},
        "line_items": [],
        "totals": {"total": 0},
        "an_unknown_section": {"x": "y"},
    }
    resp = client.post(f"/templates/{template.id}/generate", headers=auth_headers, json={"values": values})
    assert resp.status_code == 200
    stored_values = resp.json()["extracted_json"]["_values"]
    assert "totally_made_up_field" not in stored_values["header"]
    assert "an_unknown_section" not in stored_values
