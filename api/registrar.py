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
    """Idempotent: safe to call on every push. Returns a status dict — never
    raises: unexpected failures come back as {"status": "error"} so the
    member's workflow log shows something readable instead of a raw 500."""
    try:
        return _register(repo)
    except Exception as exc:  # noqa: BLE001 — surface, don't 500
        return _outcome("error", detail=f"{type(exc).__name__}: {exc}"[:300])


def _register(repo: str) -> dict:
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return _outcome("invalid_repo")
    if time.time() - _COOLDOWN.get(repo, 0) < COOLDOWN_S:
        return _outcome("cooldown", detail="recently attempted — try again in a few minutes")

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
        # Stale branch from an earlier attempt with no open PR: RESET it to main.
        # Reusing it as-is desyncs the file sha and 409s the content write
        # (bit us live 2026-08-09 after a test-cycle cleanup rewrote members.yaml).
        _gh(rtok, "PATCH", f"/repos/{REGISTRY_REPO}/git/refs/heads/{branch}",
            json={"sha": main_sha, "force": True})

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
    # Cooldown only after the expensive successful path — a failed or
    # user-fixable attempt (missing App, no consent) must not lock retries out.
    _COOLDOWN[repo] = time.time()
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


# --- deregistration (the symmetric twin) -------------------------------------

def _remove_entry(text: str, repo: str) -> str:
    """Remove the list item whose block contains `repo: <repo>` (plus its
    indented continuation lines), then collapse tripled blank lines."""
    out, removed = [], False
    blocks = re.split(r"(?m)(?=^  - )", text)
    for b in blocks:
        if not removed and b.startswith("  - ") and re.search(
                rf"(?m)^    repo: {re.escape(repo)}\s*$", b):
            removed = True
            continue
        out.append(b)
    result = "".join(out)
    return re.sub(r"\n{3,}", "\n\n", result)


def handle_deregister(repo: str) -> dict:
    """Open a registry PR removing this repo from members.yaml AND
    deployments.yaml. Consent = the repo's own manifest no longer says
    fleet.register: true (or the repo is gone — ghost-roster cleanup).
    Merging the PR removes membership; the deployments.yaml change makes
    the existing deploy-fleet reconciler tear down hosting. Never raises."""
    try:
        return _deregister(repo)
    except Exception as exc:  # noqa: BLE001
        return _outcome("error", detail=f"{type(exc).__name__}: {exc}"[:300])


def _deregister(repo: str) -> dict:
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return _outcome("invalid_repo")

    # Consent gate: a repo that still declares register: true cannot be
    # deregistered by an outsider's curl. Repo unreachable = ghost cleanup, allowed.
    try:
        token = _inst_token(repo)
        try:
            raw = _gh(token, "GET", f"/repos/{repo}/contents/agent.manifest.yaml",
                      accept="application/vnd.github.raw").text
            if ((yaml.safe_load(raw) or {}).get("fleet") or {}).get("register"):
                return _outcome(
                    "still_consented",
                    detail="agent.manifest.yaml still says fleet.register: true — set it to "
                           "false and push first (this proves the request comes from the repo owner)")
        except Exception:
            pass  # no readable manifest — fine for leaving
    except Exception:
        pass  # repo deleted / App removed — allow roster cleanup

    rtok = _inst_token(REGISTRY_REPO)
    mem = _gh(rtok, "GET", f"/repos/{REGISTRY_REPO}/contents/members.yaml").json()
    mem_text = base64.b64decode(mem["content"]).decode()
    dep = _gh(rtok, "GET", f"/repos/{REGISTRY_REPO}/contents/deployments.yaml").json()
    dep_text = base64.b64decode(dep["content"]).decode()
    pat = re.compile(rf"(?m)^    repo: {re.escape(repo)}\s*$")
    in_mem, in_dep = bool(pat.search(mem_text)), bool(pat.search(dep_text))

    if not in_mem and not in_dep:
        owner = REGISTRY_REPO.split("/")[0]
        reg_branch = f"register/{repo.replace('/', '-')}"
        prs = _gh(rtok, "GET",
                  f"/repos/{REGISTRY_REPO}/pulls?state=open&head={owner}:{reg_branch}").json()
        if prs:
            return _outcome("registration_pending", pr=prs[0]["html_url"],
                            detail="not on the roster yet — ask the admin to CLOSE the pending "
                                   "registration PR instead")
        return _outcome("not_registered")

    branch = f"deregister/{repo.replace('/', '-')}"
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
        _gh(rtok, "PATCH", f"/repos/{REGISTRY_REPO}/git/refs/heads/{branch}",
            json={"sha": main_sha, "force": True})

    for present, cur, text, fname in ((in_mem, mem, mem_text, "members.yaml"),
                                      (in_dep, dep, dep_text, "deployments.yaml")):
        if present:
            _gh(rtok, "PUT", f"/repos/{REGISTRY_REPO}/contents/{fname}", json={
                "message": f"fleet: deregister {repo} (self-service) — {fname}",
                "content": base64.b64encode(_remove_entry(text, repo).encode()).decode(),
                "sha": cur["sha"], "branch": branch,
            })

    scope = " + ".join(x for x, p in (("membership", in_mem), ("platform hosting", in_dep)) if p)
    pr = _gh(rtok, "POST", f"/repos/{REGISTRY_REPO}/pulls", json={
        "title": f"fleet: deregister {repo}",
        "head": branch, "base": "main",
        "body": (f"Self-service DEREGISTRATION for `{repo}` — removes: **{scope}**.\n\n"
                 f"Consent verified: the repo's manifest no longer says `fleet.register: true` "
                 f"(or the repo is gone).\n\n"
                 f"**Merging = removal.** The deployments.yaml change makes the deploy-fleet "
                 f"reconciler delete the Railway service and drop the route automatically. "
                 f"Closing = the member stays.\n\nRequested via `scripts/teardown.sh`."),
    }).json()
    return _outcome("pr_opened", pr=pr["html_url"], removes=scope)
