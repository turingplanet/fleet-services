"""/api/register — self-service fleet registration (RFC 001 §10, milestone M4).

Members can't open PRs on the (private) registry — their CI tokens are scoped
to their own repo, and platform secrets never cross accounts. So they send us
ONLY their repo name (public metadata), and THIS module does the write with the
platform's own credentials: it opens a members.yaml PR that an admin merges.
The PR is inert until merged — admission stays a human decision.

Reached two ways, same logic:
  • register.yml in the member repo (fires on pushes to main, manifest-gated)
  • the /register (alias /join) PR comment, via the same pipeline as /review

Consent lives in the MEMBER's repo: we act only if their agent.manifest.yaml
says fleet.register: true. And because we fetch that manifest with a fleet App
installation token, a missing App install fails fast with the install link —
solving roster-vs-keys in one flow (you can't get on the roster without the keys).
"""
from __future__ import annotations

import base64
import re
import time

import yaml

from api.review import REGISTRY_REPO, _gh, _inst_token

INSTALL_URL = "https://github.com/apps/turing-fleet-bot"
_COOLDOWN: dict[str, float] = {}          # per-repo burst guard (process-local)
COOLDOWN_S = 600


def _outcome(status: str, **kw) -> dict:
    return {"status": status, **kw}


def handle_register(repo: str) -> dict:
    """Idempotent: safe to call on every push. Returns a status dict."""
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return _outcome("invalid_repo")
    now = time.time()
    if now - _COOLDOWN.get(repo, 0) < COOLDOWN_S:
        return _outcome("cooldown", detail="recently attempted — try again in a few minutes")
    _COOLDOWN[repo] = now

    # The keys: is the fleet App installed on this repo?
    try:
        token = _inst_token(repo)
    except Exception:
        return _outcome(
            "app_not_installed",
            install_url=INSTALL_URL,
            detail=f"Install the platform App on this repo first ({INSTALL_URL}), then retry.",
        )

    # The consent: does the member's own manifest ask for registration?
    try:
        raw = _gh(token, "GET", f"/repos/{repo}/contents/agent.manifest.yaml",
                  accept="application/vnd.github.raw").text
    except Exception:
        return _outcome("no_manifest", detail="no agent.manifest.yaml at the repo root")
    manifest = yaml.safe_load(raw) or {}
    if not (manifest.get("fleet") or {}).get("register"):
        return _outcome("not_consented",
                        detail="set fleet.register: true in agent.manifest.yaml, push, then retry")

    # The roster: idempotency against members.yaml and any pending PR.
    rtok = _inst_token(REGISTRY_REPO)
    cur = _gh(rtok, "GET", f"/repos/{REGISTRY_REPO}/contents/members.yaml").json()
    text = base64.b64decode(cur["content"]).decode()
    if f"repo: {repo}\n" in text or text.rstrip().endswith(f"repo: {repo}"):
        return _outcome("already_registered")

    branch = f"register/{repo.replace('/', '-')}"
    owner = REGISTRY_REPO.split("/")[0]
    prs = _gh(rtok, "GET",
              f"/repos/{REGISTRY_REPO}/pulls?state=open&head={owner}:{branch}").json()
    if prs:
        return _outcome("pr_pending", pr=prs[0]["html_url"])

    main_sha = _gh(rtok, "GET", f"/repos/{REGISTRY_REPO}/git/ref/heads/main").json()["object"]["sha"]
    try:
        _gh(rtok, "POST", f"/repos/{REGISTRY_REPO}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": main_sha})
    except Exception:
        pass  # stale branch from an earlier attempt without an open PR — reuse it

    slug = repo.split("/")[1]
    new_text = text.rstrip("\n") + f"\n\n  - name: {slug}\n    repo: {repo}\n"
    _gh(rtok, "PUT", f"/repos/{REGISTRY_REPO}/contents/members.yaml", json={
        "message": f"fleet: register {repo} (self-service)",
        "content": base64.b64encode(new_text.encode()).decode(),
        "sha": cur["sha"],
        "branch": branch,
    })
    pr = _gh(rtok, "POST", f"/repos/{REGISTRY_REPO}/pulls", json={
        "title": f"fleet: register {repo}",
        "head": branch,
        "base": "main",
        "body": (f"Self-service registration for `{repo}` — its manifest consents "
                 f"(`fleet.register: true`), and the fleet App is installed (verified).\n\n"
                 f"**Merging = admission. Closing = decline.** Nothing happens until an admin decides.\n\n"
                 f"Requested via `register.yml` / the `/register` command (RFC 001 §10)."),
    }).json()
    return _outcome("pr_opened", pr=pr["html_url"])


def register_reply(outcome: dict) -> str:
    """Human-facing PR-comment wording for each outcome."""
    s = outcome["status"]
    if s == "pr_opened":
        return (f"📬 **Registration PR opened:** {outcome['pr']}\n\n"
                "An admin merges it to admit this repo to the fleet — nothing is automatic beyond that. "
                "Once merged: template-sync PRs, platform `/review`, and optional platform hosting.")
    if s == "pr_pending":
        return f"⏳ **Registration already requested** — awaiting admin review: {outcome['pr']}"
    if s == "already_registered":
        return "✅ This repo is **already on the fleet roster** — you're all set."
    if s == "app_not_installed":
        return (f"🔑 **One step first:** install the platform App on this repo — {outcome['install_url']} — "
                "then comment `/register` again. (An App can't grant itself access; only the repo owner can.)")
    if s == "not_consented":
        return ("✋ Your manifest says `fleet.register` is not `true` — registration is consent-gated by "
                "**your own repo**. Set it in `agent.manifest.yaml`, push, then `/register` again.")
    if s == "cooldown":
        return "🕐 A registration attempt just ran — try again in a few minutes."
    return f"🤖 Registration couldn't run: {outcome.get('detail', s)}"
