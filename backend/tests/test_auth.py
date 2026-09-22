from rate_limit import limiter


def test_register_success(client):
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "alicepass1", "name": "Alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(client):
    payload = {"email": "bob@example.com", "password": "bobpass123", "name": "Bob"}
    client.post("/auth/register", json=payload)
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"]


def test_register_password_all_letters_rejected(client):
    resp = client.post(
        "/auth/register",
        json={"email": "carol@example.com", "password": "alllettersnodigits", "name": "Carol"},
    )
    assert resp.status_code == 400
    assert "number" in resp.json()["detail"]


def test_register_password_all_digits_rejected(client):
    resp = client.post(
        "/auth/register",
        json={"email": "cindy@example.com", "password": "12345678", "name": "Cindy"},
    )
    assert resp.status_code == 400
    assert "letter" in resp.json()["detail"]


def test_register_password_too_short_rejected(client):
    resp = client.post(
        "/auth/register",
        json={"email": "cody@example.com", "password": "a1b2", "name": "Cody"},
    )
    assert resp.status_code == 400
    assert "8 characters" in resp.json()["detail"]


def test_login_success(client):
    client.post(
        "/auth/register",
        json={"email": "dave@example.com", "password": "davepass123", "name": "Dave"},
    )
    resp = client.post("/auth/login", json={"email": "dave@example.com", "password": "davepass123"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(client):
    client.post(
        "/auth/register",
        json={"email": "erin@example.com", "password": "erinpass123", "name": "Erin"},
    )
    resp = client.post("/auth/login", json={"email": "erin@example.com", "password": "wrongpassword"})
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert resp.status_code == 401
    # Same generic message as a wrong password (see auth's login() docstring in
    # CLAUDE.md) — shouldn't reveal whether the email is registered at all.
    assert resp.json()["detail"] == "Incorrect email or password."


def test_auth_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_update_profile_changes_name_and_email(client):
    reg = client.post(
        "/auth/register",
        json={"email": "gina-old@example.com", "password": "ginapass123", "name": "Gina Old"},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    resp = client.patch(
        "/auth/profile",
        headers=headers,
        json={"name": "Gina New", "email": "gina-new@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Gina New"
    assert data["email"] == "gina-new@example.com"

    me = client.get("/auth/me", headers=headers).json()
    assert me["name"] == "Gina New"
    assert me["email"] == "gina-new@example.com"


def test_update_profile_rejects_email_already_taken_by_another_user(client):
    client.post(
        "/auth/register",
        json={"email": "holly@example.com", "password": "hollypass123", "name": "Holly"},
    )
    reg = client.post(
        "/auth/register",
        json={"email": "ivan@example.com", "password": "ivanpass123", "name": "Ivan"},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    resp = client.patch(
        "/auth/profile", headers=headers, json={"name": "Ivan", "email": "holly@example.com"}
    )
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"]


def test_update_profile_allows_keeping_the_same_email(client):
    """Submitting the form without changing the email shouldn't trip the
    duplicate-email check against the user's own existing row."""
    reg = client.post(
        "/auth/register",
        json={"email": "jack@example.com", "password": "jackpass123", "name": "Jack"},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    resp = client.patch(
        "/auth/profile", headers=headers, json={"name": "Jack Renamed", "email": "jack@example.com"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Jack Renamed"


def test_auth_me_with_valid_token(client):
    reg = client.post(
        "/auth/register",
        json={"email": "frank@example.com", "password": "frankpass123", "name": "Frank"},
    )
    token = reg.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "frank@example.com"
    assert resp.json()["due_soon_threshold_days"] == 30


def test_auth_me_rejects_garbage_token(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_login_rate_limited(client):
    """See the "Rate limiting" section in CLAUDE.md — 5/minute per IP on
    POST /auth/login. Turns the (normally test-disabled, see conftest.py)
    limiter back on just for this test."""
    limiter.enabled = True
    limiter.reset()
    try:
        client.post(
            "/auth/register",
            json={"email": "grace@example.com", "password": "gracepass123", "name": "Grace"},
        )
        statuses = [
            client.post(
                "/auth/login", json={"email": "grace@example.com", "password": "wrongpassword"}
            ).status_code
            for _ in range(7)
        ]
        assert statuses == [401, 401, 401, 401, 401, 429, 429]
    finally:
        limiter.reset()
        limiter.enabled = False
