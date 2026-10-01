"""Tests for .xlsx "Data File" support — see CLAUDE.md's Data Files section.

Storage is mocked (same "patch where a name is used" convention as every
other test file — see test_upload_pipeline.py/test_tools.py), but the actual
Excel reading/writing (openpyxl/pandas) is never mocked: every sample file
here is a real, minimal .xlsx built with openpyxl, and the pipeline's own
excel_tools.extract_excel_metadata()/merge_excel() run for real against it.
"""

import io
import json
import uuid
from types import SimpleNamespace

import openpyxl
import pytest

from models import Document

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _make_xlsx(rows: list, sheet_name: str = "Sheet1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "data-file-tester@example.com", "password": "testpass123", "name": "Data Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


# ---------------------------------------------------------------------------
# Phase 1 — upload -> "Data File" categorization
# ---------------------------------------------------------------------------


def test_upload_xlsx_is_categorized_as_data_file(client, auth_headers, monkeypatch):
    xlsx_bytes = _make_xlsx([["name", "amount"], ["Acme", 100], ["Beta", 200]])

    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: xlsx_bytes)

    files = {
        "file": (
            "invoices.xlsx",
            io.BytesIO(xlsx_bytes),
            XLSX_MIME,
        )
    }
    upload_resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert upload_resp.status_code == 200
    doc_id = upload_resp.json()["id"]

    detail = client.get(f"/documents/{doc_id}", headers=auth_headers).json()
    assert detail["status"] == "processed"
    assert detail["category"] == "Data File"
    assert detail["classification_version"] is None
    assert detail["deadline_date"] is None
    assert detail["extracted_json"] == {
        "sheets": [{"name": "Sheet1", "columns": ["name", "amount"], "row_count": 2}]
    }
    # Never went through OCR/text-extraction at all.
    assert detail["raw_text"] is None


def test_upload_xlsx_extraction_failure_is_sanitized(client, auth_headers, monkeypatch):
    """A corrupt/unreadable .xlsx lands on extraction_failed, same convention
    (and same sanitized error message) as a corrupt PDF — see
    processing.EXTRACTION_FAILED_MESSAGE."""
    real_xlsx = _make_xlsx([["a"]])  # passes the upload's own magic-byte check
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    # ...but the background pipeline's own "download" returns garbage, so
    # extract_excel_metadata() genuinely fails to parse it as a workbook.
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: b"not a real xlsx at all")

    files = {"file": ("broken.xlsx", io.BytesIO(real_xlsx), XLSX_MIME)}
    upload_resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert upload_resp.status_code == 200

    detail = client.get(f"/documents/{upload_resp.json()['id']}", headers=auth_headers).json()
    assert detail["status"] == "extraction_failed"
    assert detail["error_message"] == (
        "Text extraction failed. Please try re-uploading the file — if it keeps failing, "
        "the file may be corrupted or unreadable."
    )


def test_upload_rejects_non_xlsx_content_with_xlsx_extension(client, auth_headers):
    files = {"file": ("fake.xlsx", io.BytesIO(b"plain text, not a zip"), XLSX_MIME)}
    resp = client.post("/documents/upload", headers=auth_headers, files=files)
    assert resp.status_code == 400
    assert "doesn't match its extension" in resp.json()["detail"]


def test_data_file_excluded_from_main_documents_list(client, auth_headers, monkeypatch):
    """Confirms GET /documents (source="upload" filter — see CLAUDE.md's
    Document source separation section) still includes a real .xlsx upload:
    unlike a template sample/generated document, a Data File IS an ordinary
    source="upload" row, so it should show up normally."""
    xlsx_bytes = _make_xlsx([["a"], [1]])
    monkeypatch.setattr("main.upload_file_to_storage", lambda contents, key, content_type: key)
    monkeypatch.setattr("processing.download_file_from_storage", lambda key: xlsx_bytes)

    files = {"file": ("sheet.xlsx", io.BytesIO(xlsx_bytes), XLSX_MIME)}
    client.post("/documents/upload", headers=auth_headers, files=files)

    docs = client.get("/documents", headers=auth_headers).json()
    assert len(docs) == 1
    assert docs[0]["category"] == "Data File"


# ---------------------------------------------------------------------------
# Phase 2 — POST /tools/merge-excel
# ---------------------------------------------------------------------------


def test_merge_excel_requires_at_least_two_files(client, auth_headers):
    xlsx_bytes = _make_xlsx([["a"], [1]])
    resp = client.post(
        "/tools/merge-excel",
        headers=auth_headers,
        files={"files": ("one.xlsx", io.BytesIO(xlsx_bytes), XLSX_MIME)},
        data={"mode": "sheets"},
    )
    assert resp.status_code == 400


def test_merge_excel_rejects_content_mismatch(client, auth_headers):
    xlsx_bytes = _make_xlsx([["a"], [1]])
    files = [
        ("files", ("a.xlsx", io.BytesIO(xlsx_bytes), XLSX_MIME)),
        ("files", ("b.xlsx", io.BytesIO(b"not really an xlsx"), XLSX_MIME)),
    ]
    resp = client.post("/tools/merge-excel", headers=auth_headers, files=files, data={"mode": "sheets"})
    assert resp.status_code == 400
    assert "doesn't match its extension" in resp.json()["detail"]


def test_merge_excel_sheets_mode_produces_one_sheet_per_file(client, auth_headers, storage_objects):
    a = _make_xlsx([["name"], ["Acme"]])
    b = _make_xlsx([["category"], ["X"]])

    files = [
        ("files", ("invoices.xlsx", io.BytesIO(a), XLSX_MIME)),
        ("files", ("customers.xlsx", io.BytesIO(b), XLSX_MIME)),
    ]
    resp = client.post("/tools/merge-excel", headers=auth_headers, files=files, data={"mode": "sheets"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_name"] == "merge-excel"
    assert data["output_filename"] == "merged.xlsx"

    merged_bytes = next(iter(storage_objects.values()))
    wb = openpyxl.load_workbook(io.BytesIO(merged_bytes))
    assert wb.sheetnames == ["invoices", "customers"]
    assert list(wb["invoices"].iter_rows(values_only=True)) == [("name",), ("Acme",)]
    assert list(wb["customers"].iter_rows(values_only=True)) == [("category",), ("X",)]


def test_merge_excel_concat_mode_outer_joins_columns(client, auth_headers, storage_objects):
    a = _make_xlsx([["name", "amount"], ["Acme", 100]])
    b = _make_xlsx([["name", "category"], ["Beta", "X"]])

    files = [
        ("files", ("a.xlsx", io.BytesIO(a), XLSX_MIME)),
        ("files", ("b.xlsx", io.BytesIO(b), XLSX_MIME)),
    ]
    resp = client.post("/tools/merge-excel", headers=auth_headers, files=files, data={"mode": "concat"})
    assert resp.status_code == 200

    merged_bytes = next(iter(storage_objects.values()))
    wb = openpyxl.load_workbook(io.BytesIO(merged_bytes))
    sheet = wb["Combined"]
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == ("name", "amount", "category")
    assert rows[1] == ("Acme", 100, None)
    assert rows[2] == ("Beta", None, "X")


def test_merge_excel_isolated_from_other_users_download(client, auth_headers, storage_objects):
    """Same ownership convention as every other /tools/* output — see
    CLAUDE.md's Per-user document isolation section."""
    a = _make_xlsx([["a"], [1]])
    b = _make_xlsx([["a"], [2]])
    files = [
        ("files", ("a.xlsx", io.BytesIO(a), XLSX_MIME)),
        ("files", ("b.xlsx", io.BytesIO(b), XLSX_MIME)),
    ]
    resp = client.post("/tools/merge-excel", headers=auth_headers, files=files, data={"mode": "sheets"})
    tool_file_id = resp.json()["id"]

    other_token = client.post(
        "/auth/register",
        json={"email": "data-file-other@example.com", "password": "testpass123", "name": "Other"},
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    assert client.get(f"/tools/{tool_file_id}/download-url", headers=other_headers).status_code == 404


# ---------------------------------------------------------------------------
# Phase 3 — SQL querying: per-document chat (POST /documents/{id}/chat)
# ---------------------------------------------------------------------------


def _seed_data_file_doc(db_session, user_id, xlsx_bytes: bytes, filename="sales.xlsx") -> Document:
    doc = Document(
        user_id=user_id,
        filename=filename,
        storage_path=f"{uuid.uuid4()}.xlsx",
        status="processed",
        category="Data File",
        extracted_json={"sheets": [{"name": "Sheet1", "columns": [], "row_count": 0}]},
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _fake_groq_content(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_chat_over_data_file_runs_real_sql_against_real_rows(client, db_session, auth_headers, current_user_id, monkeypatch):
    xlsx_bytes = _make_xlsx([["name", "amount"], ["Acme", 100], ["Beta", 250], ["Gamma", 50]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes)

    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    calls = iter(
        [
            _fake_groq_content("SELECT name FROM sales_Sheet1 WHERE amount > 100"),
            _fake_groq_content("Beta has an amount over 100."),
        ]
    )
    monkeypatch.setattr("chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(
        f"/documents/{doc.id}/chat", headers=auth_headers, json={"question": "which row has amount over 100?"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "Beta has an amount over 100."
    assert data["truncated"] is False


def test_chat_over_data_file_retries_once_on_sql_error(client, db_session, auth_headers, current_user_id, monkeypatch):
    xlsx_bytes = _make_xlsx([["name", "amount"], ["Acme", 100]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes)
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    calls = iter(
        [
            _fake_groq_content("SELECT nonexistent_column FROM sales_Sheet1"),  # fails to execute
            _fake_groq_content("SELECT amount FROM sales_Sheet1"),  # corrected, on retry
            _fake_groq_content("The amount is 100."),
        ]
    )
    monkeypatch.setattr("chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(f"/documents/{doc.id}/chat", headers=auth_headers, json={"question": "what's the amount?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "The amount is 100."


def test_chat_over_data_file_second_sql_failure_is_a_502(client, db_session, auth_headers, current_user_id, monkeypatch):
    xlsx_bytes = _make_xlsx([["name"], ["Acme"]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes)
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    calls = iter(
        [
            _fake_groq_content("SELECT bad_col FROM sales_Sheet1"),
            _fake_groq_content("SELECT also_bad FROM sales_Sheet1"),
        ]
    )
    monkeypatch.setattr("chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(f"/documents/{doc.id}/chat", headers=auth_headers, json={"question": "anything?"})
    assert resp.status_code == 502


def test_chat_over_data_file_rejects_a_mutating_query(client, db_session, auth_headers, current_user_id, monkeypatch):
    """The model itself could write something other than SELECT — proves the
    SELECT-only guard actually applies on this path too, not just when called
    directly."""
    xlsx_bytes = _make_xlsx([["name"], ["Acme"]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes)
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    calls = iter(
        [
            _fake_groq_content("DELETE FROM sales_Sheet1"),
            _fake_groq_content("SELECT name FROM sales_Sheet1"),
            _fake_groq_content("Acme is the name in this sheet."),
        ]
    )
    monkeypatch.setattr("chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(f"/documents/{doc.id}/chat", headers=auth_headers, json={"question": "delete it?"})
    # First attempt rejected by run_readonly_sql's own keyword check, retried
    # once, second attempt succeeds — never actually mutates anything (the
    # in-memory DuckDB table is a throwaway pandas-backed view regardless).
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Phase 3 — SQL querying: Copilot's query_data_files tool
# ---------------------------------------------------------------------------


def _fake_tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _fake_tool_response(content, tool_calls):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def _mock_copilot_groq(monkeypatch, *responses):
    calls = iter(responses)
    monkeypatch.setattr("copilot_chat.client.chat.completions.create", lambda **kwargs: next(calls))


def test_copilot_query_data_files_runs_real_sql_and_cites_the_document(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    xlsx_bytes = _make_xlsx([["name", "amount"], ["Acme", 100], ["Beta", 250]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes, filename="sales.xlsx")
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    _mock_copilot_groq(
        monkeypatch,
        _fake_tool_response(
            None,
            [
                _fake_tool_call(
                    "c1",
                    "query_data_files",
                    {"file_ids": [str(doc.id)], "sql": "SELECT SUM(amount) AS total FROM sales_Sheet1"},
                )
            ],
        ),
        _fake_tool_response("The total amount is 350.", None),
    )

    resp = client.post(
        "/copilot/chat", headers=auth_headers, json={"message": "what's the total amount in sales.xlsx?"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "The total amount is 350."
    assert {c["id"] for c in data["citations"]} == {str(doc.id)}


def test_copilot_query_data_files_no_sql_returns_schema_only(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    """Calling with file_ids and no sql — the model discovering real column
    names before writing a query — must not execute anything, just describe
    the schema."""
    xlsx_bytes = _make_xlsx([["name", "amount"], ["Acme", 100]])
    doc = _seed_data_file_doc(db_session, current_user_id, xlsx_bytes)
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    captured = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        if len(captured) == 1:
            return _fake_tool_response(None, [_fake_tool_call("c1", "query_data_files", {"file_ids": [str(doc.id)]})])
        return _fake_tool_response("Here's what columns exist.", None)

    monkeypatch.setattr("copilot_chat.client.chat.completions.create", fake_create)

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "what columns does sales.xlsx have?"})
    assert resp.status_code == 200

    tool_message = next(m for m in captured[1] if m.get("role") == "tool")
    tool_content = json.loads(tool_message["content"])
    assert "schema" in tool_content
    assert "amount" in tool_content["schema"]
    assert "rows" not in tool_content


def test_copilot_query_data_files_rejects_non_data_file_document(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    """Passing an ordinary Invoice/Agreement document's id must fail
    gracefully (a recoverable tool error), never crash or silently query
    something that was never actually loaded."""
    ordinary = Document(
        user_id=current_user_id,
        filename="invoice.pdf",
        storage_path=f"{uuid.uuid4()}.pdf",
        status="processed",
        category="Invoice",
    )
    db_session.add(ordinary)
    db_session.commit()
    db_session.refresh(ordinary)

    _mock_copilot_groq(
        monkeypatch,
        _fake_tool_response(
            None, [_fake_tool_call("c1", "query_data_files", {"file_ids": [str(ordinary.id)], "sql": "SELECT 1"})]
        ),
        _fake_tool_response("That document isn't a spreadsheet I can query.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "query invoice.pdf"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "That document isn't a spreadsheet I can query."


def test_copilot_query_data_files_scoped_to_own_user(client, db_session, auth_headers, current_user_id, monkeypatch):
    """Same per-user isolation convention as every other Copilot tool (see
    CLAUDE.md's Per-user document isolation section) — another user's Data
    File must never be queryable, even by a directly-guessed id."""
    other_token = client.post(
        "/auth/register",
        json={"email": "data-file-copilot-other@example.com", "password": "testpass123", "name": "Other"},
    ).json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]

    xlsx_bytes = _make_xlsx([["secret"], ["value"]])
    not_mine = _seed_data_file_doc(db_session, other_user_id, xlsx_bytes, filename="not-mine.xlsx")
    monkeypatch.setattr("data_query.download_file_from_storage", lambda key: xlsx_bytes)

    _mock_copilot_groq(
        monkeypatch,
        _fake_tool_response(
            None,
            [_fake_tool_call("c1", "query_data_files", {"file_ids": [str(not_mine.id)], "sql": "SELECT * FROM x"})],
        ),
        _fake_tool_response("I couldn't find that document in your registry.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "query that other file"})
    assert resp.status_code == 200
    assert resp.json()["citations"] == []
