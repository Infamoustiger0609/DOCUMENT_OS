"""Tests for `documents.source` and the two new list endpoints it powers
(GET /documents/templates, GET /documents/generated) — see CLAUDE.md's
"Document source separation" section.

Before this, a template-learning sample (POST /templates/learn-from-sample)
and a template-generated document (POST /templates/{id}/generate) were
ordinary, unmarked `documents` rows and leaked into the main "My Documents"
list (GET /documents) and its home-page counts. `source` ("upload" /
"template_sample" / "generated") fixes that: GET /documents now always
filters to source="upload", and the Editing Workspace gets its own two
scoped lists.

Groq is mocked throughout (same "patch where a name is used" convention as
test_templates.py); Storage is mocked at `templates_router`'s own imported
name. The migration's actual data-backfill SQL was verified separately
against a disposable throwaway Postgres database (not part of this suite,
since this app's tests build tables from the ORM models, not by running
Alembic migrations — see CLAUDE.md's Testing section).
"""

import io
import json
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from models import Template

FAKE_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"

INVOICE_SCHEMA = {
    "document_type": "Invoice",
    "sections": [
        {
            "id": "header",
            "title": "Header",
            "type": "fields",
            "fields": [{"key": "vendor_name", "label": "Vendor Name", "type": "string"}],
        },
    ],
}


def _make_sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "TAX INVOICE\nVendor: Sample Vendor Ltd")
    data = doc.tobytes()
    doc.close()
    return data


def _fake_groq_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _register(client, email, name="Tester"):
    resp = client.post("/auth/register", json={"email": email, "password": "testpass123", "name": name})
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    return headers, user_id


@pytest.fixture()
def auth_headers(client):
    headers, _ = _register(client, "source-tester@example.com")
    return headers


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


@pytest.fixture()
def storage_fake(monkeypatch):
    objects: dict = {}

    def fake_upload(data, key, content_type):
        objects[key] = data
        return key

    monkeypatch.setattr("templates_router.upload_file_to_storage", fake_upload)
    monkeypatch.setattr("main.upload_file_to_storage", fake_upload)
    monkeypatch.setattr(
        "main.create_signed_url",
        lambda key, expires_in: f"https://fake-signed-url.test/{key}?expires_in={expires_in}",
    )
    return objects


def _upload_plain_document(client, headers, monkeypatch, filename="plain.pdf"):
    """A real document created through the actual upload endpoint — an
    ordinary source="upload" row, for contrast against the sample/generated
    rows below."""
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: FAKE_PDF_BYTES)
    monkeypatch.setattr("processing.extract_text", lambda file_bytes, extension: "Some plain invoice text")
    monkeypatch.setattr("processing.classify_text", lambda raw_text: ("Invoice", "reasoning"))
    monkeypatch.setattr(
        "processing.extract_structured_data",
        lambda category, raw_text: {"vendor_name": "Plain Corp"},
    )
    files = {"file": (filename, io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    resp = client.post("/documents/upload", headers=headers, files=files)
    assert resp.status_code == 200
    return resp.json()["id"]


def _learn_template_sample(client, headers, monkeypatch, storage_fake_objects, category="Invoice"):
    monkeypatch.setattr("templates_router.classify_text", lambda raw_text: (category, "reasoning"))
    monkeypatch.setattr(
        "template_learning.client.chat.completions.create",
        lambda **kwargs: _fake_groq_response(json.dumps(INVOICE_SCHEMA)),
    )
    resp = client.post(
        "/templates/learn-from-sample",
        headers=headers,
        files={"file": ("sample_invoice.pdf", _make_sample_pdf(), "application/pdf")},
    )
    assert resp.status_code == 200
    return resp.json()  # TemplateOut — includes created_from_document_id


def _generate_document(client, headers, template_id):
    values = {"header": {"vendor_name": "Northwind Traders"}}
    resp = client.post(f"/templates/{template_id}/generate", headers=headers, json={"values": values})
    assert resp.status_code == 200
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# GET /documents excludes template samples and generated documents
# ---------------------------------------------------------------------------


def test_plain_upload_has_source_upload_and_appears_in_main_list(client, auth_headers, monkeypatch):
    doc_id = _upload_plain_document(client, auth_headers, monkeypatch)

    detail = client.get(f"/documents/{doc_id}", headers=auth_headers).json()
    assert detail["source"] == "upload"

    docs = client.get("/documents", headers=auth_headers).json()
    assert [d["id"] for d in docs] == [doc_id]


def test_template_sample_excluded_from_main_list_but_reachable_via_templates_list(
    client, auth_headers, monkeypatch, storage_fake
):
    template = _learn_template_sample(client, auth_headers, monkeypatch, storage_fake)
    sample_doc_id = template["created_from_document_id"]

    assert client.get("/documents", headers=auth_headers).json() == []

    sample_docs = client.get("/documents/templates", headers=auth_headers).json()
    assert [d["id"] for d in sample_docs] == [sample_doc_id]
    assert sample_docs[0]["source"] == "template_sample"

    # Still a real, fully-featured document via the single-document endpoints.
    detail = client.get(f"/documents/{sample_doc_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["source"] == "template_sample"


def test_generated_document_excluded_from_main_list_but_reachable_via_generated_list(
    client, auth_headers, monkeypatch, storage_fake, db_session, current_user_id
):
    template = Template(
        user_id=current_user_id, name="T", category="Invoice", field_schema=INVOICE_SCHEMA
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    generated_id = _generate_document(client, auth_headers, template.id)

    assert client.get("/documents", headers=auth_headers).json() == []

    generated_docs = client.get("/documents/generated", headers=auth_headers).json()
    assert [d["id"] for d in generated_docs] == [generated_id]
    assert generated_docs[0]["source"] == "generated"

    detail = client.get(f"/documents/{generated_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["source"] == "generated"

    # Download-url still works exactly like any other owned document (same
    # ownership check, same private-Storage signed-URL mechanism).
    download = client.get(f"/documents/{generated_id}/download-url", headers=auth_headers)
    assert download.status_code == 200
    assert "url" in download.json()


def test_main_list_mixes_correctly_when_all_three_sources_exist(
    client, auth_headers, monkeypatch, storage_fake, db_session, current_user_id
):
    plain_id = _upload_plain_document(client, auth_headers, monkeypatch, filename="plain2.pdf")
    template = _learn_template_sample(client, auth_headers, monkeypatch, storage_fake)
    sample_id = template["created_from_document_id"]

    template_row = db_session.query(Template).filter(Template.id == template["id"]).first()
    generated_id = _generate_document(client, auth_headers, template_row.id)

    assert {sample_id, generated_id, plain_id} == {sample_id, generated_id, plain_id}  # sanity: all distinct
    assert len({plain_id, sample_id, generated_id}) == 3

    main_list_ids = {d["id"] for d in client.get("/documents", headers=auth_headers).json()}
    assert main_list_ids == {plain_id}

    template_list_ids = {d["id"] for d in client.get("/documents/templates", headers=auth_headers).json()}
    assert template_list_ids == {sample_id}

    generated_list_ids = {d["id"] for d in client.get("/documents/generated", headers=auth_headers).json()}
    assert generated_list_ids == {generated_id}


# ---------------------------------------------------------------------------
# Per-user isolation on the two new list endpoints
# ---------------------------------------------------------------------------


def test_templates_and_generated_lists_are_scoped_to_own_user(
    client, auth_headers, monkeypatch, storage_fake, db_session
):
    _learn_template_sample(client, auth_headers, monkeypatch, storage_fake)

    other_headers, other_user_id = _register(client, "source-tester-other@example.com", "Other")
    template = Template(user_id=other_user_id, name="T", category="Invoice", field_schema=INVOICE_SCHEMA)
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    _generate_document(client, other_headers, template.id)

    # Neither user sees the other's sample/generated documents.
    assert client.get("/documents/templates", headers=other_headers).json() == []
    assert client.get("/documents/generated", headers=auth_headers).json() == []


def test_requires_auth(client):
    assert client.get("/documents/templates").status_code == 401
    assert client.get("/documents/generated").status_code == 401
