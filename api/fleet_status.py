"""/api/fleet-status — the fleet snapshot the builders portal renders.

Read-only. Everything here is derived from sources that already exist — the
registry YAML, each member repo on GitHub, the gateway's backend list — so
there is nothing to store: a snapshot is computed, kept in memory for
FLEET_STATUS_TTL seconds, and recomputed in the background once it goes stale.
A restart just means the next request computes it again.

Two views of one snapshot:
  member view   no credentials. Versions, gate, hosting, App install, and the
                to-dos a member can act on. No review quota, no admin to-dos.
  admin view    `Authorization: Bearer <FLEET_ADMIN_KEY>`. Adds per-repo review
                quota and the admin to-dos (pending approvals, quota used up,
                hosted-but-not-on-the-roster).

Credentials: the fleet App (GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY), one
installation token per owner. Without App credentials it falls back to
GITHUB_TOKEN — that is for running it on a laptop, where the App column then
reads "unknown".

The JSON shape is the contract with the portal: builders-portal/lib/types.ts.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import yaml

import config
from api.review import GH, REGISTRY_REPO, WEEKLY_DEFAULT, _app_jwt, _quota_state

# Hosted entries in deployments.yaml that are the platform's own, not member agents.
PLATFORM_SLUGS = {"fleet-services", "mcp"}
_API = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


# ── GitHub access ───────────────────────────────────────────────────────────
class _GitHub:
    """GETs against the GitHub API, using the installation token of whichever
    account owns the repo in the path (cross-account private repos need it)."""

    def __init__(self) -> None:
        self.client = httpx.Client(timeout=30, headers=_API)
        self.owner_tokens: dict[str, str] = {}
        self.fallback = os.environ.get("GITHUB_TOKEN", "")
        self.installed: set[str] | None = None  # None = no App credentials

    def close(self) -> None:
        self.client.close()

    def load_installations(self) -> None:
        """List where the App is installed: fills owner_tokens and `installed`."""
        if not (os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY")):
            return
        bearer = {"Authorization": f"Bearer {_app_jwt()}"}
        r = self.client.get(f"{GH}/app/installations", headers=bearer, params={"per_page": 100})
        r.raise_for_status()
        repos: set[str] = set()
        for inst in r.json():
            owner = (inst.get("account") or {}).get("login", "")
            t = self.client.post(inst["access_tokens_url"], headers=bearer)
            if t.status_code >= 300:
                continue
            token = t.json()["token"]
            self.owner_tokens[owner] = token
            page = 1
            while True:
                rr = self.client.get(f"{GH}/installation/repositories",
                                     headers={"Authorization": f"Bearer {token}"},
                                     params={"per_page": 100, "page": page})
                rr.raise_for_status()
                batch = rr.json().get("repositories", [])
                repos |= {x["full_name"] for x in batch}
                if len(batch) < 100:
                    break
                page += 1
        self.installed = repos

    def token_for(self, path: str) -> str:
        m = re.match(r"^/repos/([^/]+)/", path)
        return (self.owner_tokens.get(m.group(1)) if m else None) or self.fallback

    def _auth(self, path: str) -> dict[str, str]:
        token = self.token_for(path)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def get(self, path: str, **params: Any) -> Any:
        """JSON, or None on 404."""
        r = self.client.get(f"{GH}{path}", params=params, headers=self._auth(path))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def file(self, repo: str, path: str) -> str | None:
        p = f"/repos/{repo}/contents/{path}"
        r = self.client.get(f"{GH}{p}", headers={**self._auth(p), "Accept": "application/vnd.github.raw+json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    def check_runs(self, repo: str, sha: str) -> list[dict] | None:
        """Check runs need `checks: read`. Try the owner's token, then anonymous
        (enough for a public repo). None = no way to read them."""
        path = f"/repos/{repo}/commits/{sha}/check-runs"
        for headers in (self._auth(path), {}):
            r = self.client.get(f"{GH}{path}", params={"per_page": 50}, headers=headers)
            if r.status_code == 200:
                return r.json().get("check_runs", [])
            if r.status_code not in (401, 403, 404):
                r.raise_for_status()
            if not headers:
                break
        return None


# ── registry files ──────────────────────────────────────────────────────────
def _entries(doc: Any, key: str) -> list[dict]:
    """Accept both `key: [ {...} ]` and `key: { slug: {...} }`."""
    if isinstance(doc, dict):
        doc = doc.get(key, doc)
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    if isinstance(doc, dict):
        return [{"slug": k, **(v or {})} for k, v in doc.items() if isinstance(v, (dict, type(None)))]
    return []


def parse_members(raw: str) -> list[dict]:
    out = []
    for m in _entries(yaml.safe_load(raw) or {}, "members"):
        repo = m.get("repo")
        slug = m.get("slug") or m.get("name") or (repo.split("/")[-1] if repo else None)
        if not repo or not slug:
            continue
        limit = (m.get("ai_review") or {}).get("weekly_limit", WEEKLY_DEFAULT)
        out.append({"slug": slug, "repo": repo, "review_limit": int(limit), "entry": m})
    return out


def parse_deployments(raw: str | None) -> dict[str, dict]:
    """Indexed by slug AND by repo. `host: platform` is a hosting mode, not a
    hostname — the real host is <slug>.<FLEET_DOMAIN>."""
    out: dict[str, dict] = {}
    for d in _entries(yaml.safe_load(raw or "") or {}, "deployments"):
        slug = d.get("slug") or d.get("name")
        if not slug:
            continue
        host = d.get("domain") or d.get("hostname") or f"{slug}.{config.FLEET_DOMAIN}"
        entry = {**d, "slug": slug, "host": host}
        out[slug] = entry
        if d.get("repo"):
            out[d["repo"]] = entry
    return out


# ── versions ────────────────────────────────────────────────────────────────
def _semver(tag: str) -> tuple:
    m = re.match(r"^v?(\d+(?:\.\d+)*)", tag)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (-1,)


def _tags(g: _GitHub, repo: str) -> list[str]:
    """Newest first. GitHub's own /tags order is not by version."""
    return sorted((t["name"] for t in g.get(f"/repos/{repo}/tags", per_page=100) or []), key=_semver, reverse=True)


def template_version(answers: str | None, all_tags: list[str]) -> tuple[str, int]:
    """(version, how many tags behind). Copier records `_commit`: a tag, a
    `git describe` string like v0.4.1-3-gabcdef, or a bare sha."""
    if not answers:
        return ("—", 0)
    commit = str((yaml.safe_load(answers) or {}).get("_commit", "—"))
    if commit in all_tags:
        return (commit, all_tags.index(commit))
    m = re.match(r"^(v?\d+(?:\.\d+)*)", commit)
    if m and m.group(1) in all_tags:
        return (commit, all_tags.index(m.group(1)))
    return (commit[:10], 0)


def policies_version(workflow: str | None, latest: str) -> tuple[str, bool]:
    m = re.search(r"policies/[^\n@]*@(v\d+(?:\.\d+)*)", workflow or "")
    if not m:
        return ("—", False)
    return (m.group(1), _semver(m.group(1)) < _semver(latest))


# ── main gate ───────────────────────────────────────────────────────────────
def gate_of(runs: list[dict]) -> str | None:
    gate = [r for r in runs if config.GATE_CHECK_NAME in (r.get("name") or "").lower()]
    if not gate:
        return None
    conclusions = {r.get("conclusion") for r in gate}
    if conclusions & {"failure", "cancelled", "timed_out", "action_required"}:
        return "fail"
    if conclusions <= {"success", "skipped", "neutral"}:
        return "pass"
    return "none"  # still running


def main_gate(g: _GitHub, repo: str, branch: str) -> tuple[str, str | None]:
    """The gate only runs on PRs, so: the head commit's own checks if any,
    else the checks of the PR that merged it. Neither = pushed straight to main.
    Returns (pass|fail|none, reason)."""
    head = g.get(f"/repos/{repo}/commits/{branch}")
    if not head:
        return ("none", "no-commits")
    runs = g.check_runs(repo, head["sha"])
    if runs is None:
        return ("none", "no-checks-access")
    found = gate_of(runs)
    if found:
        return (found, None)
    merged = [p for p in g.get(f"/repos/{repo}/commits/{head['sha']}/pulls", per_page=10) or [] if p.get("merged_at")]
    if not merged:
        return ("none", "direct-push")
    runs = g.check_runs(repo, merged[0]["head"]["sha"])
    if runs is None:
        return ("none", "no-checks-access")
    found = gate_of(runs)
    return (found or "none", None if found else f"pr-{merged[0]['number']}-no-gate")


# ── gateway, pending PRs ────────────────────────────────────────────────────
def live_backends(client: httpx.Client) -> tuple[set[str], dict[str, str], bool]:
    """(slugs the gateway serves, slug → error, gateway reachable)."""
    try:
        r = client.get(f"{config.GATEWAY_URL}/api/backends", timeout=15)
        r.raise_for_status()
        data = r.json()
        roster = data.get("roster") or []
        slugs = {b if isinstance(b, str) else (b.get("slug") or b.get("name") or "") for b in roster}
        return (slugs - {""}, {k: str(v) for k, v in (data.get("errors") or {}).items()}, True)
    except Exception:  # noqa: BLE001 — an unreachable gateway is a status, not a crash
        return (set(), {}, False)


def pending_prs(g: _GitHub) -> list[dict]:
    out = []
    now = dt.datetime.now(dt.timezone.utc)
    for pr in g.get(f"/repos/{REGISTRY_REPO}/pulls", state="open", per_page=50) or []:
        files = {f["filename"] for f in g.get(f"/repos/{REGISTRY_REPO}/pulls/{pr['number']}/files") or []}
        kind = "register" if "members.yaml" in files else "deploy" if "deployments.yaml" in files else None
        if not kind:
            continue
        m = re.search(r"([\w.-]+/[\w.-]+)", pr["title"] + " " + (pr.get("body") or ""))
        opened = dt.datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        out.append({"number": pr["number"], "kind": kind, "subject": m.group(1) if m else pr["title"],
                    "url": pr["html_url"], "age_days": (now - opened).days})
    return out


# ── one member row ──────────────────────────────────────────────────────────
def _member_row(g: _GitHub, m: dict, ctx: dict) -> tuple[dict | None, list[dict]]:
    repo, slug = m["repo"], m["slug"]
    meta = g.get(f"/repos/{repo}")
    if meta is None:
        return None, [_todo("admin", slug, "repo can't be read", "on the roster, but missing on GitHub or the App has no access",
                            f"https://github.com/{repo}", "open repo")]
    branch = meta.get("default_branch", "main")
    version, behind = template_version(g.file(repo, ".copier-answers.yml"), ctx["template_tags"])
    workflow = g.file(repo, ".github/workflows/review.yml") or g.file(repo, ".github/workflows/platform-review.yml")
    policies, policies_old = policies_version(workflow, ctx["latest_policies"])
    app = "unknown" if g.installed is None else "installed" if repo in g.installed else "missing"
    gate, gate_why = main_gate(g, repo, branch)

    deploy: dict[str, Any] = {"status": "none"}
    d = ctx["deployments"].get(repo) or ctx["deployments"].get(slug)
    if d:
        if d["slug"] in ctx["gateway_errors"]:
            deploy = {"status": "down", "host": d["host"]}
        elif d["slug"] in ctx["live"]:
            deploy = {"status": "live" if gate == "pass" else "unchecked", "host": d["host"]}
        else:
            deploy = {"status": "pending", "host": d["host"]}
    pr = ctx["pending"].get(repo) or ctx["pending"].get(slug)
    if pr:
        deploy = {"status": "pending" if pr["kind"] == "deploy" else "none", "prNumber": pr["number"]}

    review = None
    token = g.token_for(f"/repos/{repo}/")
    if token and app != "missing":
        try:
            used, limit = _quota_state(token, repo, ctx["members_by_repo"])
            review = {"used": used, "limit": limit}
        except Exception:  # noqa: BLE001 — quota is a nice-to-have column
            review = None

    row = {"slug": slug, "repo": repo, "templateVersion": version, "templateBehind": behind,
           "policiesVersion": policies, "policiesOutdated": policies_old, "app": app, "mainGate": gate,
           "deploy": deploy, "review": review, "lastActivity": meta.get("pushed_at")}

    todos = []
    unchecked = "production is running an unchecked version" if deploy["status"] == "unchecked" else None
    if gate == "fail":
        todos.append(_todo("member", slug, f"last gate on {branch} failed", unchecked,
                           f"https://github.com/{repo}/actions", "view runs"))
    elif gate_why == "direct-push" and unchecked:
        todos.append(_todo("member", slug, f"latest commit on {branch} skipped the gate (direct push)", unchecked,
                           f"https://github.com/{repo}/commits/{branch}", "view commits"))
    if deploy["status"] == "down":
        todos.append(_todo("member", slug, "hosted, but the gateway can't reach it",
                           ctx["gateway_errors"].get(d["slug"], "")[:120] if d else None,
                           f"https://{deploy['host']}/api/health", "check health"))
    if app == "missing":
        todos.append(_todo("member", slug, "fleet App is not installed", "registered, but every sync silently skips this repo",
                           "https://agents.turingplanet.ai/#install", "how to install"))
    if behind >= 3 or policies_old:
        behind_by = [f"template {behind} releases behind"] if behind >= 3 else []
        if policies_old:
            behind_by.append(f"policies {policies}, latest is {ctx['latest_policies']}")
        todos.append(_todo("member", slug, "scaffold or policies far behind", ", ".join(behind_by),
                           f"https://github.com/{repo}/pulls?q=is%3Apr+sync", "view sync PRs"))
    if review and review["limit"] and review["used"] >= review["limit"]:
        todos.append(_todo("admin", slug, "review quota used up this week", f"{review['used']} / {review['limit']}",
                           f"https://github.com/{REGISTRY_REPO}/blob/main/members.yaml", "adjust quota"))
    return row, todos


def _todo(audience: str, subject: str, action: str, detail: str | None, href: str, label: str) -> dict:
    t = {"audience": audience, "subject": subject, "action": action, "href": href, "linkLabel": label}
    if detail:
        t["detail"] = detail
    return t


# ── the snapshot ────────────────────────────────────────────────────────────
def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_snapshot() -> dict:
    """The full (admin) snapshot. Costs roughly 8 GitHub calls per member."""
    now = dt.datetime.now(dt.timezone.utc)
    g = _GitHub()
    try:
        g.load_installations()
        template_tags, policies_tags = _tags(g, config.TEMPLATE_REPO), _tags(g, config.POLICIES_REPO)
        roster = parse_members(g.file(REGISTRY_REPO, "members.yaml") or "")
        deployments = parse_deployments(g.file(REGISTRY_REPO, "deployments.yaml"))
        live, gateway_errors, gateway_ok = live_backends(g.client)
        prs = pending_prs(g)
        ctx = {
            "template_tags": template_tags,
            "latest_policies": policies_tags[0] if policies_tags else "—",
            "deployments": deployments, "live": live, "gateway_errors": gateway_errors,
            "pending": {p["subject"]: p for p in prs},
            "members_by_repo": {m["repo"]: m["entry"] for m in roster},
        }
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda m: _member_row(g, m, ctx), roster))
    finally:
        g.close()

    members = [row for row, _ in results if row]
    todos = [t for _, ts in results for t in ts]
    roster_repos = {m["repo"] for m in roster}
    for slug, d in deployments.items():
        if slug == d["slug"] and slug not in PLATFORM_SLUGS and d.get("repo") not in roster_repos:
            todos.append(_todo("admin", slug, "hosted in deployments.yaml but not on the members.yaml roster", d.get("repo"),
                               f"https://github.com/{REGISTRY_REPO}/blob/main/deployments.yaml", "view deployments.yaml"))
    for p in prs:
        todos.insert(0, _todo("admin", p["subject"],
                              "approve registration" if p["kind"] == "register" else "approve hosting request",
                              f"PR #{p['number']}, open {p['age_days']} days", p["url"], "open PR"))
    return {
        "generatedAt": _iso(now),
        "nextRunAt": _iso(now + dt.timedelta(seconds=config.FLEET_STATUS_TTL)),
        "latest": {"template": template_tags[0] if template_tags else "—", "policies": ctx["latest_policies"]},
        "gatewayOnline": gateway_ok,
        "summary": {
            "members": len(members),
            "deployed": sum(x["deploy"]["status"] in ("live", "unchecked", "down") for x in members),
            "outdated": sum(x["templateBehind"] > 0 or x["policiesOutdated"] for x in members),
            "pending": len(prs),
            "reviewUsed": sum(x["review"]["used"] for x in members if x["review"]),
            "reviewLimit": sum(x["review"]["limit"] for x in members if x["review"]),
        },
        "todos": todos,
        "members": members,
    }


def view(snapshot: dict, admin: bool) -> dict:
    """What a caller gets. The member view drops review quota and admin to-dos."""
    todos = [{k: v for k, v in t.items() if k != "audience"}
             for t in snapshot["todos"] if admin or t.get("audience") == "member"]
    if admin:
        return {**snapshot, "view": "admin", "todos": todos}
    return {
        **snapshot, "view": "member", "todos": todos,
        "summary": {**snapshot["summary"], "reviewUsed": 0, "reviewLimit": 0},
        "members": [{**m, "review": None} for m in snapshot["members"]],
    }


# ── cache: serve what we have, refresh behind it ────────────────────────────
_lock = threading.Lock()          # one build at a time
RETRY_AFTER = 60                  # seconds before retrying a failed background rebuild
_state: dict[str, Any] = {"snapshot": None, "at": 0.0, "refreshing": False}


def _refresh() -> None:
    try:
        with _lock:
            snapshot = build_snapshot()
            _state.update(snapshot=snapshot, at=time.monotonic())
    except Exception:  # noqa: BLE001 — keep serving the stale snapshot, retry in a minute, not on every request
        _state["at"] = time.monotonic() - config.FLEET_STATUS_TTL + RETRY_AFTER
    finally:
        _state["refreshing"] = False


def cached_snapshot() -> dict:
    """Fresh → return it. Stale → return it and rebuild in the background.
    Nothing yet → build now (concurrent first callers wait on the same build).
    A failed rebuild keeps the stale snapshot; a failed first build raises."""
    snapshot, age = _state["snapshot"], time.monotonic() - _state["at"]
    if snapshot is not None:
        if age > config.FLEET_STATUS_TTL and not _state["refreshing"]:
            _state["refreshing"] = True
            threading.Thread(target=_refresh, daemon=True).start()
        return snapshot
    with _lock:
        if _state["snapshot"] is None:
            _state.update(snapshot=build_snapshot(), at=time.monotonic())
        return _state["snapshot"]


def configured() -> bool:
    return bool((os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY"))
                or os.environ.get("GITHUB_TOKEN"))


def allowed_origin(origin: str | None) -> str | None:
    """The CORS origin to echo back, if the caller's is on the list."""
    return origin if origin and origin.rstrip("/") in config.FLEET_STATUS_ORIGINS else None
