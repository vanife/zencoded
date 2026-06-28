import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from zencoded.web import auth as authmod  # noqa: E402
from zencoded.web.app import app  # noqa: E402
from zencoded.web.models import UserView  # noqa: E402

ORIGIN = "http://localhost:8000"
CSRF = {"X-Requested-With": "zencoded", "Origin": ORIGIN}


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_anonymous_shows_login(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Sign in with GitHub" in r.text


def test_jobs_requires_auth(client):
    r = client.post("/jobs", json={"url": "https://example.com/x"}, headers=CSRF)
    assert r.status_code == 401


def test_jobs_csrf_header_required(client):
    app.dependency_overrides[authmod.require_user] = lambda: UserView(login="tester")
    try:
        r = client.post("/jobs", json={"url": "https://example.com/x"}, headers={"Origin": ORIGIN})
        assert r.status_code == 403
        assert "CSRF" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_jobs_cross_origin_rejected(client):
    app.dependency_overrides[authmod.require_user] = lambda: UserView(login="tester")
    try:
        r = client.post(
            "/jobs",
            json={"url": "https://example.com/x"},
            headers={"X-Requested-With": "zencoded", "Origin": "http://evil.com"},
        )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_unknown_job_404(client):
    app.dependency_overrides[authmod.require_user] = lambda: UserView(login="tester")
    try:
        assert client.get("/jobs/does-not-exist").status_code == 404
    finally:
        app.dependency_overrides.clear()
