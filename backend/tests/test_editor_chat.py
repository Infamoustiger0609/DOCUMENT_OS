"""Tests for POST /editor/chat (Phase 23, see CLAUDE.md's Editor assistant
section). Groq itself is always mocked (`editor_chat.client.chat.completions.create`)
— same reasoning as chat.py/classification.py's own tests never hitting a real
Groq API: no real cost, no network dependency, and full control over the
exact tool_calls/plain-text sequence needed to exercise the agentic loop
(single call, no-tool clarifying question, and multi-round chaining).

merge_pdf/split_pdf run against the real pikepdf logic (no system binary
needed); compress_pdf/docx_to_pdf/ocr_pdf mock the underlying transform
function itself, same as test_tools.py, since Ghostscript/LibreOffice/
Tesseract aren't installed in this CI environment.
"""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pikepdf
import pymupdf as fitz  # PyMuPDF's `fitz` import name is deprecated in favor of `pymupdf`
import pytest

from models import ToolFile


def _make_pdf(text: str, page_count: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} (page {i + 1})")
    data = doc.tobytes()
    doc.close()
    return data


def _fake_tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _fake_response(content: str | None, tool_calls: list | None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "editor-chat-tester@example.com", "password": "testpass123", "name": "Chat Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _get_tool_file(db_session, tool_file_id: str) -> ToolFile:
    return db_session.query(ToolFile).filter(ToolFile.id == uuid.UUID(tool_file_id)).one()


def test_no_tool_call_returns_plain_reply(client, auth_headers, monkeypatch):
    """Ambiguous/off-topic requests: Groq answers directly, no tool ever runs."""
    monkeypatch.setattr(
        "editor_chat.client.chat.completions.create",
        lambda **kwargs: _fake_response("What size would you like the photo resized to?", None),
    )

    resp = client.post("/editor/chat", headers=auth_headers, data={"message": "resize my photo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "What size would you like the photo resized to?"
    assert data["tool_runs"] == []


def test_single_tool_call_merge(client, auth_headers, storage_objects, db_session, monkeypatch):
    calls = iter(
        [
            _fake_response(None, [_fake_tool_call("call_1", "merge_pdf", {"files": ["a.pdf", "b.pdf"]})]),
            _fake_response("Merged a.pdf and b.pdf into one file.", None),
        ]
    )
    monkeypatch.setattr("editor_chat.client.chat.completions.create", lambda **kwargs: next(calls))

    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    resp = client.post(
        "/editor/chat", headers=auth_headers, data={"message": "merge a.pdf and b.pdf"}, files=files
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Merged a.pdf and b.pdf into one file."
    assert len(data["tool_runs"]) == 1
    run = data["tool_runs"][0]
    assert run["tool_name"] == "merge_pdf"
    assert run["error"] is None
    assert len(run["files"]) == 1
    assert run["files"][0]["output_filename"] == "merged.pdf"

    tool_file = _get_tool_file(db_session, run["files"][0]["id"])
    with pikepdf.open(io.BytesIO(storage_objects[tool_file.storage_path])) as pdf:
        assert len(pdf.pages) == 2


def test_chained_merge_then_compress(client, auth_headers, storage_objects, monkeypatch):
    """The exact scenario from the task spec: 'merge these, then compress the
    result' — compress_pdf's argument in round 2 references "merged.pdf", the
    file merge_pdf produced in round 1, proving the agentic loop actually
    chains rather than just running one call per message."""
    calls = iter(
        [
            _fake_response(None, [_fake_tool_call("call_1", "merge_pdf", {"files": ["a.pdf", "b.pdf"]})]),
            _fake_response(
                None,
                [_fake_tool_call("call_2", "compress_pdf", {"file": "merged.pdf", "target_size_kb": 5})],
            ),
            _fake_response("Merged both files and compressed the result.", None),
        ]
    )
    monkeypatch.setattr("editor_chat.client.chat.completions.create", lambda **kwargs: next(calls))
    # compress_pdf itself needs Ghostscript — mock it, same as test_tools.py.
    monkeypatch.setattr("editor_chat.compress_pdf", lambda data, quality: b"%PDF-1.4 compressed-fake")

    files = [
        ("files", ("a.pdf", io.BytesIO(_make_pdf("A")), "application/pdf")),
        ("files", ("b.pdf", io.BytesIO(_make_pdf("B")), "application/pdf")),
    ]
    resp = client.post(
        "/editor/chat",
        headers=auth_headers,
        data={"message": "merge these and compress the result under 5kb"},
        files=files,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tool_runs"]) == 2
    assert data["tool_runs"][0]["tool_name"] == "merge_pdf"
    assert data["tool_runs"][1]["tool_name"] == "compress_pdf"
    assert data["tool_runs"][1]["files"][0]["output_filename"] == "compressed_merged.pdf"
    assert data["reply"] == "Merged both files and compressed the result."


def test_referencing_unknown_file_is_recoverable(client, auth_headers, storage_objects, monkeypatch):
    """The model hallucinates/typos a filename that was never uploaded —
    the tool call must fail gracefully (an error fed back to the model) and
    the model's next response (mocked here) becomes the final reply, not a
    500 or a crash."""
    calls = iter(
        [
            _fake_response(None, [_fake_tool_call("call_1", "compress_pdf", {"file": "nonexistent.pdf"})]),
            _fake_response("I couldn't find 'nonexistent.pdf' among your files. Could you re-check the name?", None),
        ]
    )
    monkeypatch.setattr("editor_chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(
        "/editor/chat", headers=auth_headers, data={"message": "compress nonexistent.pdf"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "couldn't find" in data["reply"]
    assert data["tool_runs"][0]["error"] is not None
    assert data["tool_runs"][0]["files"] == []


def test_context_tool_files_lets_a_later_turn_reference_a_prior_result(
    client, auth_headers, storage_objects, db_session, monkeypatch
):
    """Cross-turn chaining: a file produced in an earlier chat turn (or even a
    manual /tools/* run) can be referenced in a later message via
    context_tool_files, without re-uploading its bytes — resolved from
    Storage by editor_chat.ChatFileContext.resolve()."""
    # Seed a prior result directly, the way an earlier turn (or a manual tool
    # run) would have left it.
    me = client.get("/auth/me", headers=auth_headers).json()
    prior_bytes = _make_pdf("Prior result", page_count=2)
    storage_path = "tool-outputs/prior/prior.pdf"
    storage_objects[storage_path] = prior_bytes
    prior = ToolFile(
        user_id=uuid.UUID(me["id"]),
        tool_name="merge-pdf",
        original_filename="a.pdf, b.pdf",
        output_filename="merged.pdf",
        storage_path=storage_path,
        mime_type="application/pdf",
        size_bytes=len(prior_bytes),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(prior)
    db_session.commit()

    calls = iter(
        [
            _fake_response(None, [_fake_tool_call("call_1", "split_pdf", {"file": "merged.pdf"})]),
            _fake_response("Split merged.pdf into its 2 pages.", None),
        ]
    )
    monkeypatch.setattr("editor_chat.client.chat.completions.create", lambda **kwargs: next(calls))

    resp = client.post(
        "/editor/chat",
        headers=auth_headers,
        data={
            "message": "split merged.pdf into pages",
            "context_tool_files": json.dumps([{"id": str(prior.id), "filename": "merged.pdf"}]),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tool_runs"][0]["files"]) == 2  # 2-page PDF -> 2 split outputs


def test_requires_auth(client):
    resp = client.post("/editor/chat", data={"message": "merge a.pdf and b.pdf"})
    assert resp.status_code == 401


def test_rejects_empty_message(client, auth_headers):
    resp = client.post("/editor/chat", headers=auth_headers, data={"message": "   "})
    assert resp.status_code == 400


def test_rejects_too_many_files(client, auth_headers):
    files = [
        ("files", (f"f{i}.pdf", io.BytesIO(_make_pdf(str(i))), "application/pdf")) for i in range(16)
    ]
    resp = client.post(
        "/editor/chat", headers=auth_headers, data={"message": "merge all of these"}, files=files
    )
    assert resp.status_code == 400
    assert "Too many files" in resp.json()["detail"]


def test_rejects_content_extension_mismatch(client, auth_headers):
    files = [("files", ("fake.pdf", io.BytesIO(b"not a real pdf"), "application/pdf"))]
    resp = client.post(
        "/editor/chat", headers=auth_headers, data={"message": "compress fake.pdf"}, files=files
    )
    assert resp.status_code == 400
