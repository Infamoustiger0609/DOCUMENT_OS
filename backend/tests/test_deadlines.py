import uuid
from datetime import date, timedelta

import pytest

from models import Document


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/auth/register",
        json={"email": "deadline-tester@example.com", "password": "testpass123", "name": "Tester"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def current_user_id(client, auth_headers):
    return client.get("/auth/me", headers=auth_headers).json()["id"]


def _make_doc(db_session, user_id, deadline_date=None, filename="test.pdf", category=None, status="processed"):
    doc = Document(
        user_id=user_id,
        filename=filename,
        storage_path=f"{uuid.uuid4()}.pdf",
        status=status,
        category=category,
        deadline_date=deadline_date,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_deadlines_requires_auth(client):
    resp = client.get("/documents/deadlines")
    assert resp.status_code == 401


def test_deadlines_excludes_documents_with_no_deadline(client, db_session, auth_headers, current_user_id):
    _make_doc(db_session, current_user_id, deadline_date=None)
    resp = client.get("/documents/deadlines", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_deadlines_overdue_urgency_filter(client, db_session, auth_headers, current_user_id):
    today = date.today()
    overdue = _make_doc(db_session, current_user_id, filename="overdue.pdf", deadline_date=today - timedelta(days=5))
    future = _make_doc(db_session, current_user_id, filename="future.pdf", deadline_date=today + timedelta(days=5))

    resp = client.get("/documents/deadlines", params={"urgency": "overdue"}, headers=auth_headers)

    assert resp.status_code == 200
    ids = [d["id"] for d in resp.json()]
    assert str(overdue.id) in ids
    assert str(future.id) not in ids


def test_deadlines_due_soon_urgency_filter(client, db_session, auth_headers, current_user_id):
    """The backend's own urgency=due-soon param hardcodes a 30-day window
    server-side (see CLAUDE.md's Settings section — the real per-user
    due_soon_threshold_days is applied client-side instead, since no current
    frontend page calls this param)."""
    today = date.today()
    soon = _make_doc(db_session, current_user_id, filename="soon.pdf", deadline_date=today + timedelta(days=10))
    far = _make_doc(db_session, current_user_id, filename="far.pdf", deadline_date=today + timedelta(days=90))
    overdue = _make_doc(db_session, current_user_id, filename="overdue2.pdf", deadline_date=today - timedelta(days=1))

    resp = client.get("/documents/deadlines", params={"urgency": "due-soon"}, headers=auth_headers)

    ids = [d["id"] for d in resp.json()]
    assert str(soon.id) in ids
    assert str(far.id) not in ids
    assert str(overdue.id) not in ids


def test_deadlines_sorted_soonest_first(client, db_session, auth_headers, current_user_id):
    today = date.today()
    later = _make_doc(db_session, current_user_id, filename="later.pdf", deadline_date=today + timedelta(days=20))
    overdue = _make_doc(db_session, current_user_id, filename="overdue3.pdf", deadline_date=today - timedelta(days=3))
    soonest_upcoming = _make_doc(
        db_session, current_user_id, filename="soonest.pdf", deadline_date=today + timedelta(days=1)
    )

    resp = client.get("/documents/deadlines", headers=auth_headers)

    ids_in_order = [d["id"] for d in resp.json()]
    assert ids_in_order == [str(overdue.id), str(soonest_upcoming.id), str(later.id)]


def test_deadlines_invalid_urgency_rejected(client, auth_headers):
    resp = client.get("/documents/deadlines", params={"urgency": "nonsense"}, headers=auth_headers)
    assert resp.status_code == 400


def test_deadlines_response_omits_raw_text(client, db_session, auth_headers, current_user_id):
    """See the Performance notes section in CLAUDE.md — list endpoints use
    DocumentListOut, which drops raw_text."""
    doc = _make_doc(db_session, current_user_id, deadline_date=date.today())
    doc.raw_text = "this should never appear in the list response"
    db_session.add(doc)
    db_session.commit()

    resp = client.get("/documents/deadlines", headers=auth_headers)

    assert resp.status_code == 200
    assert "raw_text" not in resp.json()[0]
