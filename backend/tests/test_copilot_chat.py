"""Tests for POST /copilot/chat (Phase 25, see CLAUDE.md's Copilot section).
Groq itself (`copilot_chat.client.chat.completions.create`) is always mocked
— same reasoning as chat.py/classification.py/editor_chat.py's own tests: no
real API cost, full control over the exact tool_calls sequence needed to
prove the agentic loop and each tool's real database query logic (all four
tools run for real against the test Postgres container, including genuine
Postgres full-text search — nothing about the DB layer is mocked)."""

import json
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from copilot_chat import MAX_LIST_RESULTS
from models import Document


def _make_doc(db_session, user_id, **kwargs):
    defaults = dict(
        filename="test.pdf",
        storage_path=f"{uuid.uuid4()}.pdf",
        status="processed",
        category=None,
        deadline_date=None,
        raw_text=None,
    )
    defaults.update(kwargs)
    doc = Document(user_id=user_id, **defaults)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _fake_tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _fake_response(content, tool_calls):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "copilot-tester@example.com", "password": "testpass123", "name": "Copilot Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


def _mock_groq(monkeypatch, *responses):
    calls = iter(responses)
    monkeypatch.setattr("copilot_chat.client.chat.completions.create", lambda **kwargs: next(calls))


def test_no_tool_call_returns_plain_reply(client, auth_headers, monkeypatch):
    _mock_groq(monkeypatch, _fake_response("Hi! Ask me about your documents.", None))
    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Hi! Ask me about your documents."
    assert data["citations"] == []


def test_list_documents_filters_by_category(client, db_session, auth_headers, current_user_id, monkeypatch):
    agreement = _make_doc(db_session, current_user_id, filename="agreement.pdf", category="Agreement")
    _make_doc(db_session, current_user_id, filename="invoice.pdf", category="Invoice")

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "list_documents", {"category": "Agreement"})]),
        _fake_response("You have 1 active agreement: agreement.pdf.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "show all active agreements"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "You have 1 active agreement: agreement.pdf."
    assert len(data["citations"]) == 1
    assert data["citations"][0]["id"] == str(agreement.id)
    assert data["citations"][0]["filename"] == "agreement.pdf"


def test_get_document_count_uncapped_beyond_list_limit(client, db_session, auth_headers, current_user_id, monkeypatch):
    """The exact regression this fixes: a plain 'how many total files do I
    have' must use get_document_count, and that count must be a real,
    uncapped SELECT count(*) — not something that silently truncates at
    list_documents' own MAX_LIST_RESULTS, which would quietly under-report
    once a real registry grows past it."""
    total = MAX_LIST_RESULTS + 5
    for i in range(total):
        _make_doc(db_session, current_user_id, filename=f"doc{i}.pdf")

    captured_messages = []

    def fake_create(**kwargs):
        captured_messages.append(kwargs["messages"])
        if len(captured_messages) == 1:
            return _fake_response(None, [_fake_tool_call("c1", "get_document_count", {})])
        return _fake_response(f"You have {total} documents in total.", None)

    monkeypatch.setattr("copilot_chat.client.chat.completions.create", fake_create)

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "how many total files do I have?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == f"You have {total} documents in total."
    assert data["citations"] == []  # a bare count has nothing specific to cite

    tool_message = next(m for m in captured_messages[1] if m.get("role") == "tool")
    tool_content = json.loads(tool_message["content"])
    assert tool_content["count"] == total


def test_get_document_count_filtered_by_category(client, db_session, auth_headers, current_user_id, monkeypatch):
    _make_doc(db_session, current_user_id, filename="inv1.pdf", category="Invoice")
    _make_doc(db_session, current_user_id, filename="inv2.pdf", category="Invoice")
    _make_doc(db_session, current_user_id, filename="agr1.pdf", category="Agreement")

    captured_messages = []

    def fake_create(**kwargs):
        captured_messages.append(kwargs["messages"])
        if len(captured_messages) == 1:
            return _fake_response(None, [_fake_tool_call("c1", "get_document_count", {"category": "Invoice"})])
        return _fake_response("You have 2 invoices.", None)

    monkeypatch.setattr("copilot_chat.client.chat.completions.create", fake_create)

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "how many invoices do I have?"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "You have 2 invoices."

    tool_message = next(m for m in captured_messages[1] if m.get("role") == "tool")
    assert json.loads(tool_message["content"])["count"] == 2


def test_get_deadline_summary_respects_user_threshold(client, db_session, auth_headers, current_user_id, monkeypatch):
    today = date.today()
    overdue = _make_doc(db_session, current_user_id, filename="overdue_invoice.pdf", category="Invoice", deadline_date=today - timedelta(days=5))
    _make_doc(db_session, current_user_id, filename="far_future.pdf", category="Agreement", deadline_date=today + timedelta(days=200))

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "get_deadline_summary", {})]),
        _fake_response("You have 1 overdue document: overdue_invoice.pdf.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "which invoices are overdue?"})
    assert resp.status_code == 200
    data = resp.json()
    citation_ids = {c["id"] for c in data["citations"]}
    assert str(overdue.id) in citation_ids
    assert "far_future.pdf" not in [c["filename"] for c in data["citations"]]


def test_search_document_text_uses_real_full_text_search(client, db_session, auth_headers, current_user_id, monkeypatch):
    matching = _make_doc(
        db_session,
        current_user_id,
        filename="exclusive_deal.pdf",
        category="Agreement",
        raw_text="This agreement grants exclusivity to the distributor for the covered territory.",
    )
    _make_doc(
        db_session,
        current_user_id,
        filename="unrelated.pdf",
        category="Invoice",
        raw_text="Invoice for office supplies, quantity 10, total $500.",
    )

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "search_document_text", {"query": "exclusivity"})]),
        _fake_response("One document mentions exclusivity: exclusive_deal.pdf.", None),
    )

    resp = client.post(
        "/copilot/chat", headers=auth_headers, json={"message": "find contracts mentioning exclusivity"}
    )
    assert resp.status_code == 200
    data = resp.json()
    citation_ids = {c["id"] for c in data["citations"]}
    assert str(matching.id) in citation_ids
    assert len(data["citations"]) == 1


def test_search_document_text_no_matches_is_honest(client, db_session, auth_headers, current_user_id, monkeypatch):
    _make_doc(db_session, current_user_id, filename="plain.pdf", raw_text="Nothing special in here.")

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "search_document_text", {"query": "exclusivity"})]),
        _fake_response("No documents contain the term 'exclusivity'.", None),
    )

    resp = client.post(
        "/copilot/chat", headers=auth_headers, json={"message": "find contracts mentioning exclusivity"}
    )
    assert resp.status_code == 200
    assert resp.json()["citations"] == []


def test_chained_list_then_deadline_summary(client, db_session, auth_headers, current_user_id, monkeypatch):
    today = date.today()
    agreement = _make_doc(db_session, current_user_id, filename="deal.pdf", category="Agreement", deadline_date=today + timedelta(days=5))

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "list_documents", {"category": "Agreement"})]),
        _fake_response(None, [_fake_tool_call("c2", "get_deadline_summary", {})]),
        _fake_response("Your agreement deal.pdf is due soon.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "tell me about my agreements and deadlines"})
    assert resp.status_code == 200
    data = resp.json()
    # same document surfaced by both tool calls -> deduped, not doubled
    assert len([c for c in data["citations"] if c["id"] == str(agreement.id)]) == 1


def test_unknown_tool_call_is_recoverable(client, auth_headers, monkeypatch):
    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "delete_everything", {})]),
        _fake_response("I don't have a tool for that.", None),
    )
    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "delete everything"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "I don't have a tool for that."


def test_invalid_date_argument_is_recoverable(client, auth_headers, monkeypatch):
    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "list_documents", {"deadline_before": "not-a-date"})]),
        _fake_response("That date didn't parse — could you confirm the date you meant?", None),
    )
    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "list docs due before whenever"})
    assert resp.status_code == 200
    assert "date" in resp.json()["reply"].lower()


def test_citations_scoped_to_own_user(client, db_session, auth_headers, current_user_id, monkeypatch):
    """Per-user isolation (see CLAUDE.md's Per-user document isolation
    section) must hold for the copilot's tools too, not just the /documents/*
    endpoints — a document belonging to a different user must never surface,
    even if it would otherwise match the filter/search."""
    other_user_resp = client.post(
        "/auth/register",
        json={"email": "someone-else-copilot@example.com", "password": "testpass123", "name": "Someone Else"},
    )
    other_user_id = other_user_resp.json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_user_id}"}).json()["id"]
    _make_doc(db_session, other_user_id, filename="not-mine.pdf", category="Agreement")
    mine = _make_doc(db_session, current_user_id, filename="mine.pdf", category="Agreement")

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "list_documents", {"category": "Agreement"})]),
        _fake_response("You have 1 agreement.", None),
    )

    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "show my agreements"})
    filenames = [c["filename"] for c in resp.json()["citations"]]
    assert filenames == ["mine.pdf"]
    assert mine.filename in filenames


def test_requires_auth(client):
    resp = client.post("/copilot/chat", json={"message": "hello"})
    assert resp.status_code == 401


def test_rejects_empty_message(client, auth_headers):
    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "   "})
    assert resp.status_code == 400


def test_rejects_too_long_message(client, auth_headers):
    resp = client.post("/copilot/chat", headers=auth_headers, json={"message": "x" * 2001})
    assert resp.status_code == 400
