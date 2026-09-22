"""Tests for GET /analytics/schema-summary and POST /analytics/generate
(Phase 28, see CLAUDE.md's Analytics section). Groq is always mocked
(`analytics.client.chat.completions.create`) — same reasoning as every other
AI endpoint's tests in this app. Nothing about the schema-summary computation
or the aggregation tools is mocked — they run for real against the test
Postgres container, over real seeded `extracted_json` data."""

import json
import uuid
from types import SimpleNamespace

import pytest

from models import Document


def _make_doc(db_session, user_id, category, extracted_json=None, filename="test.pdf"):
    doc = Document(
        user_id=user_id,
        filename=filename,
        storage_path=f"{uuid.uuid4()}.pdf",
        status="processed",
        category=category,
        extracted_json=extracted_json,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _fake_tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _fake_response(content, tool_calls):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def _mock_groq(monkeypatch, *responses):
    calls = iter(responses)
    monkeypatch.setattr("analytics.client.chat.completions.create", lambda **kwargs: next(calls))


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "analytics-tester@example.com", "password": "testpass123", "name": "Analytics Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


# ---------------------------------------------------------------------------
# GET /analytics/schema-summary
# ---------------------------------------------------------------------------


def test_schema_summary_requires_auth(client):
    resp = client.get("/analytics/schema-summary")
    assert resp.status_code == 401


def test_schema_summary_empty_registry(client, auth_headers):
    resp = client.get("/analytics/schema-summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"total_documents": 0, "categories": []}


def test_schema_summary_infers_real_field_types(client, db_session, auth_headers, current_user_id):
    _make_doc(
        db_session, current_user_id, "Invoice",
        extracted_json={
            "vendor_name": "Acme Corp",
            "amount": 1200.50,
            "due_date": "2026-12-01",
            "gst_number": None,  # never populated -> must NOT appear in the summary
        },
    )
    _make_doc(
        db_session, current_user_id, "Invoice",
        extracted_json={"vendor_name": "Beta LLC", "amount": 300.0, "due_date": "2026-11-15"},
    )
    # A failed/parse-error extraction must be excluded entirely, not counted
    # as if "amount" were populated.
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"parse_error": True, "raw_response": "oops"})

    resp = client.get("/analytics/schema-summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_documents"] == 3

    invoice = next(c for c in data["categories"] if c["category"] == "Invoice")
    assert invoice["document_count"] == 3
    assert invoice["structured_document_count"] == 2  # parse_error row excluded

    fields_by_name = {f["name"]: f for f in invoice["fields"]}
    assert fields_by_name["amount"] == {"name": "amount", "type": "numeric", "populated_count": 2}
    assert fields_by_name["vendor_name"] == {"name": "vendor_name", "type": "string", "populated_count": 2}
    assert fields_by_name["due_date"] == {"name": "due_date", "type": "date", "populated_count": 2}
    assert "gst_number" not in fields_by_name  # never populated -> not listed
    assert "parse_error" not in fields_by_name
    assert "raw_response" not in fields_by_name


def test_schema_summary_scoped_to_own_user(client, db_session, auth_headers, current_user_id):
    other_token = client.post(
        "/auth/register",
        json={"email": "someone-else-analytics@example.com", "password": "testpass123", "name": "Someone Else"},
    ).json()["access_token"]
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]
    _make_doc(db_session, other_user_id, "Invoice", extracted_json={"amount": 999.0})

    resp = client.get("/analytics/schema-summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"total_documents": 0, "categories": []}


# ---------------------------------------------------------------------------
# POST /analytics/generate
# ---------------------------------------------------------------------------


def test_generate_skips_groq_when_no_data(client, auth_headers, monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("Groq should never be called when there is no data to work with")

    monkeypatch.setattr("analytics.client.chat.completions.create", fail_if_called)

    resp = client.post("/analytics/generate", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"] == []
    assert data["cached"] is False


def test_generate_real_metric_from_real_aggregation(client, db_session, auth_headers, current_user_id, monkeypatch):
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"vendor_name": "Acme", "amount": 100.0})
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"vendor_name": "Beta", "amount": 300.0})

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "amount", "operation": "avg"})]),
        _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [{"label": "Average invoice value", "value": 200.0, "format": "currency", "category": "Invoice"}]
            })],
        ),
    )

    resp = client.post("/analytics/generate", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is False
    assert data["metrics"] == [{"label": "Average invoice value", "value": 200.0, "format": "currency", "category": "Invoice"}]


def test_generate_drops_a_fabricated_metric_value(client, db_session, auth_headers, current_user_id, monkeypatch):
    """The core safety guarantee: a metric whose value doesn't match any real
    tool-call result this run must be dropped, not trusted on the model's word."""
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 100.0})
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 300.0})

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "amount", "operation": "avg"})]),
        _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [
                    {"label": "Average invoice value", "value": 200.0, "format": "currency", "category": "Invoice"},
                    {"label": "Made-up invoice metric", "value": 9999.0, "format": "currency", "category": "Invoice"},
                ]
            })],
        ),
    )

    resp = client.post("/analytics/generate", headers=auth_headers)
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["label"] == "Average invoice value"


def test_generate_rejects_field_not_in_schema_summary(client, db_session, auth_headers, current_user_id, monkeypatch):
    """A tool call referencing a field that doesn't actually exist/isn't
    populated must fail gracefully (an error fed back, no crash) and
    contribute no known values — proving the whitelist is real, not just a
    prompt suggestion."""
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 100.0})

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "totally_made_up_field", "operation": "sum"})]),
        _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [{"label": "Fake metric", "value": 42.0, "format": "count", "category": "Invoice"}]
            })],
        ),
    )

    resp = client.post("/analytics/generate", headers=auth_headers)
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    # tool_calls_made stays 0 (the only call errored) -> zero real aggregation
    # behind the model's proposed metric -> that specific metric is refused.
    assert all(m["label"] != "Fake metric" for m in metrics)
    # But the category-coverage fallback (fill_missing_category_metrics)
    # still surfaces this real Invoice document via its own document_count —
    # a deterministic, code-computed number, not the model's fabricated one —
    # rather than showing nothing at all for a category that genuinely has data.
    assert metrics == [{"label": "Invoice documents", "value": 1.0, "format": "count", "category": "Invoice"}]


def test_generate_fills_in_a_category_the_model_never_explored(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    """Regression test for a real bug: the model's own tool-calling
    exploration is not reliably deterministic across runs — verified live
    against real data (see CLAUDE.md's Analytics section) that 4 of 5 real
    runs against the same schema summary each omitted a different category
    entirely. This proves the code-level backstop (_fill_missing_category_
    metrics), not the prompt, is what actually guarantees every category
    with real documents shows up — here the model only ever touches
    Invoice, never Agreement, yet Agreement must still appear."""
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 250.0})
    _make_doc(db_session, current_user_id, "Agreement", extracted_json={"auto_renewal": True})

    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "amount", "operation": "sum"})]),
        _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [{"label": "Total invoiced", "value": 250.0, "format": "currency", "category": "Invoice"}]
            })],
        ),
    )

    resp = client.post("/analytics/generate", headers=auth_headers)
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    by_category = {m["category"]: m for m in metrics}

    assert by_category["Invoice"] == {"label": "Total invoiced", "value": 250.0, "format": "currency", "category": "Invoice"}
    # Agreement was never touched by any tool call, yet still appears —
    # via the deterministic document_count fallback, not a model guess.
    assert by_category["Agreement"] == {"label": "Agreement documents", "value": 1.0, "format": "count", "category": "Agreement"}


def test_generate_caches_and_force_bypasses(client, db_session, auth_headers, current_user_id, monkeypatch):
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 100.0})

    call_count = 0

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "amount", "operation": "sum"})])
        return _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [{"label": "Total invoiced", "value": 100.0, "format": "currency", "category": "Invoice"}]
            })],
        )

    monkeypatch.setattr("analytics.client.chat.completions.create", fake_create)

    first = client.post("/analytics/generate", headers=auth_headers)
    assert first.json()["cached"] is False
    calls_after_first = call_count

    second = client.post("/analytics/generate", headers=auth_headers)
    assert second.json()["cached"] is True
    assert second.json()["metrics"] == first.json()["metrics"]
    assert call_count == calls_after_first  # no new Groq calls for the cached response

    third = client.post("/analytics/generate?force=true", headers=auth_headers)
    assert third.json()["cached"] is False
    assert call_count > calls_after_first  # force bypassed the cache and called Groq again


def test_generate_requires_auth(client):
    resp = client.post("/analytics/generate")
    assert resp.status_code == 401


def test_generate_falls_back_on_total_groq_failure_and_is_not_cached(
    client, db_session, auth_headers, current_user_id, monkeypatch
):
    """Regression test for a real failure mode hit live while verifying the
    category-coverage fix above: Groq's own daily rate limit (a real 429,
    not a mocked one) made generate_analytics() raise entirely, and the
    router's top-level except block returned a bare empty result — even
    though real documents existed. Fixed to fall back to
    fill_missing_category_metrics here too; also confirms the failure is
    never cached (unlike a successful generation), so the very next call
    gets a real result once Groq recovers rather than being stuck showing
    the fallback for the rest of the 10-minute cache window."""
    _make_doc(db_session, current_user_id, "Invoice", extracted_json={"amount": 100.0})
    _make_doc(db_session, current_user_id, "Agreement", extracted_json={"auto_renewal": True})

    def boom(**kwargs):
        raise RuntimeError("simulated total Groq failure")

    monkeypatch.setattr("analytics.client.chat.completions.create", boom)
    first = client.post("/analytics/generate", headers=auth_headers)
    assert first.status_code == 200
    data = first.json()
    assert data["cached"] is False
    by_category = {m["category"]: m for m in data["metrics"]}
    assert by_category["Invoice"] == {"label": "Invoice documents", "value": 1.0, "format": "count", "category": "Invoice"}
    assert by_category["Agreement"] == {"label": "Agreement documents", "value": 1.0, "format": "count", "category": "Agreement"}

    # Groq "recovers" — a second call (still without force=true) must NOT
    # reuse the failed attempt's result, proving it was never cached.
    _mock_groq(
        monkeypatch,
        _fake_response(None, [_fake_tool_call("c1", "aggregate_numeric_field", {"category": "Invoice", "field_name": "amount", "operation": "sum"})]),
        _fake_response(
            None,
            [_fake_tool_call("c2", "propose_metrics", {
                "metrics": [{"label": "Total invoiced", "value": 100.0, "format": "currency", "category": "Invoice"}]
            })],
        ),
    )
    second = client.post("/analytics/generate", headers=auth_headers)
    assert second.json()["cached"] is False
    assert {"label": "Total invoiced", "value": 100.0, "format": "currency", "category": "Invoice"} in second.json()["metrics"]
