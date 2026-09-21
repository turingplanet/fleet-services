"""/api/fleet-status — cached fleet snapshot, member view open, admin view by key."""
import time

import pytest

import config
from api import fleet_status as fs


# --- parsing ------------------------------------------------------------------
def test_parse_members_reads_name_and_weekly_limit():
    raw = """
members:
  - name: hello-fleet
    repo: enochhz/hello-fleet
    ai_review: { weekly_limit: 5 }
  - repo: a/two
  - name: broken
"""
    got = fs.parse_members(raw)
    assert [(m["slug"], m["repo"], m["review_limit"]) for m in got] == [
        ("hello-fleet", "enochhz/hello-fleet", 5),
        ("two", "a/two", 2),                                   # slug from the repo name, platform default limit
    ]


def test_parse_deployments_platform_mode_gets_a_real_host():
    raw = """
deployments:
  - slug: hello-fleet
    repo: enochhz/hello-fleet
    host: platform
  - slug: custom
    domain: agent.example.com
"""
    d = fs.parse_deployments(raw)
    assert d["hello-fleet"]["host"] == "hello-fleet.agents.turingplanet.ai"
    assert d["enochhz/hello-fleet"] is d["hello-fleet"]        # indexed by repo too
    assert d["custom"]["host"] == "agent.example.com"
    assert fs.parse_deployments(None) == {}


def test_template_version_counts_tags_behind():
    tags = ["v0.0.27", "v0.0.26", "v0.0.25"]
    assert fs.template_version("_commit: v0.0.27\n", tags) == ("v0.0.27", 0)
    assert fs.template_version("_commit: v0.0.25\n", tags) == ("v0.0.25", 2)
    assert fs.template_version("_commit: v0.0.26-3-gabcdef1\n", tags) == ("v0.0.26-3-gabcdef1", 1)
    assert fs.template_version("_commit: 0123456789abcdef\n", tags) == ("0123456789", 0)
    assert fs.template_version(None, tags) == ("—", 0)


def test_policies_version_compares_numerically():
    wf = "    uses: turingplanet/policies/.github/workflows/review.yml@v0.0.9\n"
    assert fs.policies_version(wf, "v0.0.10") == ("v0.0.9", True)   # 9 < 10, not a string compare
    assert fs.policies_version(wf, "v0.0.9") == ("v0.0.9", False)
    assert fs.policies_version("no pin here", "v1") == ("—", False)


def test_gate_of():
    run = lambda name, conclusion: {"name": name, "conclusion": conclusion}  # noqa: E731
    assert fs.gate_of([run("review / review", "success")]) == "pass"
    assert fs.gate_of([run("review / review", "success"), run("review / lint", "failure")]) == "fail"
    assert fs.gate_of([run("review / review", None)]) == "none"            # still running
    assert fs.gate_of([run("register", "success")]) is None                # no gate among the checks


# --- views --------------------------------------------------------------------
SNAPSHOT = {
    "generatedAt": "2026-09-20T00:00:00Z", "nextRunAt": "2026-09-20T00:10:00Z",
    "latest": {"template": "v0.0.27", "policies": "v0.0.9"}, "gatewayOnline": True,
    "summary": {"members": 1, "deployed": 1, "outdated": 0, "pending": 1, "reviewUsed": 2, "reviewLimit": 5},
    "todos": [
        {"audience": "admin", "subject": "a/one", "action": "approve registration", "href": "h", "linkLabel": "open PR"},
        {"audience": "member", "subject": "one", "action": "fleet App is not installed", "href": "h", "linkLabel": "how"},
    ],
    "members": [{"slug": "one", "repo": "a/one", "review": {"used": 2, "limit": 5}, "mainGate": "pass"}],
}


def test_member_view_hides_quota_and_admin_todos():
    v = fs.view(SNAPSHOT, admin=False)
    assert v["view"] == "member"
    assert [t["action"] for t in v["todos"]] == ["fleet App is not installed"]
    assert all("audience" not in t for t in v["todos"])
    assert v["members"][0]["review"] is None and v["members"][0]["mainGate"] == "pass"
    assert (v["summary"]["reviewUsed"], v["summary"]["reviewLimit"]) == (0, 0)
    assert SNAPSHOT["members"][0]["review"] == {"used": 2, "limit": 5}     # the cached snapshot is not mutated


def test_admin_view_keeps_everything():
    v = fs.view(SNAPSHOT, admin=True)
    assert v["view"] == "admin" and len(v["todos"]) == 2
    assert v["members"][0]["review"] == {"used": 2, "limit": 5}
    assert v["summary"]["reviewUsed"] == 2


# --- cache --------------------------------------------------------------------
@pytest.fixture
def fresh_cache(monkeypatch):
    monkeypatch.setitem(fs._state, "snapshot", None)
    monkeypatch.setitem(fs._state, "at", 0.0)
    monkeypatch.setitem(fs._state, "refreshing", False)


def _wait_idle():
    for _ in range(200):
        if not fs._state["refreshing"]:
            return
        time.sleep(0.01)
    raise AssertionError("background refresh never finished")


def test_cache_builds_once_then_serves_from_memory(monkeypatch, fresh_cache):
    calls = []
    monkeypatch.setattr(fs, "build_snapshot", lambda: calls.append(1) or {"n": len(calls)})
    assert fs.cached_snapshot() == {"n": 1}
    assert fs.cached_snapshot() == {"n": 1}
    assert len(calls) == 1


def test_stale_snapshot_is_served_while_a_refresh_runs(monkeypatch, fresh_cache):
    calls = []
    monkeypatch.setattr(fs, "build_snapshot", lambda: calls.append(1) or {"n": len(calls)})
    fs.cached_snapshot()
    fs._state["at"] = time.monotonic() - config.FLEET_STATUS_TTL - 1
    assert fs.cached_snapshot() == {"n": 1}                    # the caller never waits for the rebuild
    _wait_idle()
    assert fs.cached_snapshot() == {"n": 2}


def test_failed_refresh_keeps_the_stale_snapshot_and_backs_off(monkeypatch, fresh_cache):
    monkeypatch.setattr(fs, "build_snapshot", lambda: {"n": 1})
    fs.cached_snapshot()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("github is down")

    monkeypatch.setattr(fs, "build_snapshot", boom)
    fs._state["at"] = time.monotonic() - config.FLEET_STATUS_TTL - 1
    assert fs.cached_snapshot() == {"n": 1}
    _wait_idle()
    assert fs.cached_snapshot() == {"n": 1}                    # still served
    _wait_idle()
    assert len(calls) == 1                                     # and not retried on every request


def test_first_build_failure_raises(monkeypatch, fresh_cache):
    def boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(fs, "build_snapshot", boom)
    with pytest.raises(RuntimeError):
        fs.cached_snapshot()


# --- endpoint -----------------------------------------------------------------
def _client(monkeypatch, admin_key="k", creds=True):
    from fastapi.testclient import TestClient
    from mcp_server.server import build_http_app

    monkeypatch.setattr(config, "FLEET_ADMIN_KEY", admin_key)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    if creds:
        monkeypatch.setenv("GITHUB_APP_ID", "1")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "x")
    else:
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(fs, "cached_snapshot", lambda: SNAPSHOT)
    return TestClient(build_http_app())


def test_endpoint_member_view_needs_no_key(monkeypatch):
    r = _client(monkeypatch).get("/api/fleet-status")
    assert r.status_code == 200
    assert r.json()["view"] == "member" and r.json()["members"][0]["review"] is None
    assert r.headers["cache-control"] == "public, max-age=60"


def test_endpoint_admin_view_with_key(monkeypatch):
    r = _client(monkeypatch).get("/api/fleet-status", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200 and r.json()["view"] == "admin"
    assert r.json()["members"][0]["review"] == {"used": 2, "limit": 5}
    assert "no-store" in r.headers["cache-control"]


def test_endpoint_wrong_key_is_401_not_a_silent_member_view(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/fleet-status", headers={"Authorization": "Bearer nope"}).status_code == 401
    # no admin key configured: an offered key can never be right
    assert _client(monkeypatch, admin_key="").get(
        "/api/fleet-status", headers={"Authorization": "Bearer x"}).status_code == 401


def test_endpoint_cors_only_for_listed_origins(monkeypatch):
    c = _client(monkeypatch)
    ok = c.get("/api/fleet-status", headers={"Origin": "https://builders.turingplanet.ai"})
    assert ok.headers["access-control-allow-origin"] == "https://builders.turingplanet.ai"
    assert "Origin" in ok.headers["vary"]
    other = c.get("/api/fleet-status", headers={"Origin": "https://evil.example"})
    assert other.status_code == 200 and "access-control-allow-origin" not in other.headers


def test_endpoint_503_without_credentials(monkeypatch):
    r = _client(monkeypatch, creds=False).get("/api/fleet-status")
    assert r.status_code == 503 and r.json()["status"] == "not_configured"


def test_endpoint_503_when_the_first_build_fails(monkeypatch):
    c = _client(monkeypatch)

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(fs, "cached_snapshot", boom)
    r = c.get("/api/fleet-status")
    assert r.status_code == 503 and r.json() == {"status": "unavailable", "error": "RuntimeError"}
