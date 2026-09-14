"""/api/quota — read-only per-repo review quota for the admin status page.

Reuses the exact counting the /review path uses (`_quota_state`: MARKER
comments in the last 7 days vs `ai_review.weekly_limit`), so the status
page shows the same numbers a member would see in their PR footer.

Also reports whether the fleet App is installed on each roster repo — the
token mint is the same call the registrar uses as its "keys" check — so the
status page can flag registered-but-not-installed repos (those are silently
skipped by every sync run).

Auth: `Authorization: Bearer <FLEET_ADMIN_KEY>`; the endpoint is disabled
(503) until that variable is set on the service.
"""
from __future__ import annotations

import hmac

from api.review import _inst_token, _members, _quota_state


def quota_report() -> dict[str, dict]:
    """{repo: {"used": n, "limit": n, "app_installed": bool}} for every roster
    repo. A repo the App can't reach (not installed / gone) still gets a row,
    with app_installed=False and no counts — never a partial failure that
    hides the rest of the fleet."""
    members = _members()
    out: dict[str, dict] = {}
    for repo, entry in members.items():
        limit = int(((entry or {}).get("ai_review") or {}).get("weekly_limit", _default_limit()))
        try:
            token = _inst_token(repo)
        except Exception as exc:  # noqa: BLE001 — App not installed, repo gone, …
            out[repo] = {"used": None, "limit": limit, "app_installed": False,
                         "error": f"{type(exc).__name__}"[:80]}
            continue
        try:
            used, limit = _quota_state(token, repo, members)
            out[repo] = {"used": used, "limit": limit, "app_installed": True}
        except Exception as exc:  # noqa: BLE001
            out[repo] = {"used": None, "limit": limit, "app_installed": True,
                         "error": f"{type(exc).__name__}"[:80]}
    return out


def _default_limit() -> int:
    from api.review import WEEKLY_DEFAULT
    return WEEKLY_DEFAULT


def check_admin(authorization: str | None, admin_key: str) -> bool:
    """Constant-time bearer comparison. Empty admin_key = endpoint disabled."""
    if not admin_key or not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), admin_key)
