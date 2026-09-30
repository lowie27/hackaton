"""Authentication and hardening of the web API (no lifespan: uses the seeded dev database)."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import requires_db
from kb.web import app as web

pytestmark = requires_db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web, "ADMIN_PASSWORD", "s3cret-test")
    web._hits.clear()
    return TestClient(web.app, base_url="https://testserver")


def test_security_headers_and_no_public_docs(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_admin_login_needs_the_password(client):
    assert client.post("/api/login", json={"email": "admin@example.com"}).status_code == 403
    assert client.post("/api/login", json={"email": "admin@example.com", "password": "wrong"}).status_code == 403
    assert client.post("/api/login", json={"email": "admin@example.com", "password": "s3cret-test"}).status_code == 200
    assert client.get("/api/me").json()["is_admin"]


def test_member_cannot_reset_even_with_the_password(client):
    client.post("/api/login", json={"email": "bram@example.com"})
    assert client.post("/api/admin/reset", json={"password": "s3cret-test"}).status_code == 403


def test_forged_or_expired_cookies_are_rejected(client):
    client.post("/api/login", json={"email": "bram@example.com"})
    uid, expires, sig = client.cookies.get(web.COOKIE).split(".")
    for forged in (f"1.{expires}.{sig}", f"{uid}.{int(expires) + 999}.{sig}", web._sign(int(uid), int(time.time()) - 1)):
        client.cookies.set(web.COOKIE, forged)
        assert client.get("/api/me").status_code == 401


def test_user_id_in_the_body_is_ignored(client):
    client.post("/api/login", json={"email": "bram@example.com"})
    me = client.get("/api/me").json()
    r = client.post("/api/search", json={"query": "holiday", "user_id": 1, "top_k": 5})
    assert r.status_code == 200 and client.get("/api/me").json()["email"] == me["email"] == "bram@example.com"


def test_login_is_rate_limited(client):
    codes = [client.post("/api/login", json={"email": "nobody@example.com"}).status_code for _ in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


def test_request_sizes_are_bounded(client):
    client.post("/api/login", json={"email": "bram@example.com"})
    assert client.post("/api/search", json={"query": "x", "top_k": 10_000}).status_code == 422
    assert client.post("/api/search", json={"query": "x" * 501}).status_code == 422
