"""/api/quota — admin read-only quota + App-installed report."""
import config
from api import quota as q


def test_check_admin_requires_exact_bearer():
    assert q.check_admin("Bearer s3cret", "s3cret")
    assert q.check_admin("bearer s3cret", "s3cret")           # scheme case-insensitive
    assert not q.check_admin("Bearer wrong", "s3cret")
    assert not q.check_admin("Basic s3cret", "s3cret")
    assert not q.check_admin(None, "s3cret")
    assert not q.check_admin("Bearer s3cret", "")             # unset key = disabled


def test_quota_report_shapes(monkeypatch):
    members = {
        "a/one": {"repo": "a/one", "ai_review": {"weekly_limit": 5}},
        "b/two": {"repo": "b/two"},                            # platform default limit
        "c/gone": {"repo": "c/gone"},                          # App not installed
    }
    monkeypatch.setattr(q, "_members", lambda: members)

    def inst_token(repo):
        if repo == "c/gone":
            raise RuntimeError("404 no installation")
        return "tok-" + repo

    monkeypatch.setattr(q, "_inst_token", inst_token)
    monkeypatch.setattr(q, "_quota_state",
                        lambda token, repo, m: (3 if repo == "a/one" else 0,
                                                 int(((m[repo]).get("ai_review") or {}).get("weekly_limit", 2))))
    rep = q.quota_report()
    assert rep["a/one"] == {"used": 3, "limit": 5, "app_installed": True}
    assert rep["b/two"] == {"used": 0, "limit": 2, "app_installed": True}
    assert rep["c/gone"]["app_installed"] is False and rep["c/gone"]["used"] is None
    assert rep["c/gone"]["limit"] == 2


def _client(monkeypatch, admin_key, app_creds=True):
    from fastapi.testclient import TestClient
    from mcp_server.server import build_http_app

    monkeypatch.setattr(config, "FLEET_ADMIN_KEY", admin_key)
    if app_creds:
        monkeypatch.setenv("GITHUB_APP_ID", "1")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "x")
    else:
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    return TestClient(build_http_app())


def test_endpoint_disabled_without_admin_key(monkeypatch):
    r = _client(monkeypatch, "").get("/api/quota", headers={"Authorization": "Bearer x"})
    assert r.status_code == 503 and r.json()["status"] == "not_configured"


def test_endpoint_disabled_without_app_creds(monkeypatch):
    r = _client(monkeypatch, "k", app_creds=False).get("/api/quota", headers={"Authorization": "Bearer k"})
    assert r.status_code == 503


def test_endpoint_rejects_bad_key(monkeypatch):
    c = _client(monkeypatch, "k")
    assert c.get("/api/quota").status_code == 401
    assert c.get("/api/quota", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_endpoint_returns_report(monkeypatch):
    monkeypatch.setattr(q, "quota_report", lambda: {"a/one": {"used": 1, "limit": 5, "app_installed": True}})
    r = _client(monkeypatch, "k").get("/api/quota", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "quota": {"a/one": {"used": 1, "limit": 5, "app_installed": True}}}
