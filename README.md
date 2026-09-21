# fleet-services

> **Platform-side services for 图灵星球 Agent 军团.** Overview: **https://github.com/turingplanet/agent-legion**

Holds the credentials members must never see (the platform's Anthropic key, the fleet GitHub App), so member CI only ever sends **public metadata** and the platform acts on its own side.

Live at `https://fleet-services.agents.turingplanet.ai` — deployed like any member agent, through the registry's `deployments.yaml`.

## What it serves

**`POST /api/review`** — platform-paid on-demand AI review ([RFC 002](https://github.com/turingplanet/agent-legion/blob/main/rfcs/002-platform-ai-review.md)). A member comments `/review [security|perf|general|help]` on their PR; a trigger workflow in their repo forwards `{repo, pr_number, comment_id}`. This service then:

1. verifies the comment exists, is a `/review` command, and its author is a repo collaborator;
2. checks the repo is on the fleet roster (`members.yaml` in agent-registry, read via the App);
3. checks the weekly quota (`ai_review.weekly_limit`) — counted from hidden markers on past review comments, so **GitHub is the database**;
4. caps the diff size;
5. runs the platform's Claude with the review persona and posts an **advisory** comment via the App.

You get feedback immediately: a 👀 reaction plus a "🔍 Review in progress" comment within seconds, which is then **edited in place** into the final review (~1–2 min) — one comment total, no notification spam. Reviews never block a PR — the member's own gate decides. Help and decline replies carry a different marker so they never consume quota. Any unexpected failure is reported on the PR: silent failure is banned.

**`POST /api/register`** — self-service fleet registration ([RFC 001 §10](https://github.com/turingplanet/agent-legion/blob/main/rfcs/001-migration-and-deploy.md)). Send `{repo}` (public metadata); the service verifies the fleet App is installed on that repo (the keys) and that its `agent.manifest.yaml` says `fleet.register: true` (the consent), then opens a members.yaml PR on the registry with platform credentials. **Merging = admission** — nothing happens until an admin decides. Reached from `register.yml` (fires on pushes to main) or the `/register` / `/join` PR comment, which rides the same pipeline as `/review`. Idempotent against the roster and pending PRs; per-repo cooldown.

**`POST /api/deregister`** — the symmetric exit (see `scripts/teardown.sh` in the template). Consent is verified server-side: the repo's manifest must no longer say `fleet.register: true` (or the repo is gone — ghost cleanup). One PR removes the repo from members.yaml **and** deployments.yaml; merging it makes the deploy-fleet reconciler tear down platform hosting automatically.

**`GET /api/quota`** — admin-only, read-only. `{ "<owner/repo>": { "used": n, "limit": n, "app_installed": bool } }` for every roster repo, using the exact same marker counting as `/review` (plus whether the fleet App can reach the repo — the registrar's "keys" check). Consumed by the [fleet-status](https://github.com/turingplanet/fleet-status) admin page from the registry's weekly run. Auth: `Authorization: Bearer $FLEET_ADMIN_KEY`; replies `503 not_configured` until that variable is set on the service. It only reads GitHub — nothing here writes.

**`GET /api/fleet-status`** — the fleet snapshot the builders portal renders: per member repo, scaffold and policies versions, App installed, the gate result of what is on main, hosting vs what the gateway can reach, plus a to-do list. Derived on request from the registry, GitHub and the gateway, kept in memory for `FLEET_STATUS_TTL` seconds and rebuilt in the background once stale (a failed rebuild keeps serving the last one). No key → the **member view** (no review quota, no admin to-dos), with CORS for `FLEET_STATUS_ORIGINS`. `Authorization: Bearer <FLEET_ADMIN_KEY>` → the **admin view**; a wrong key is a 401, never a silent member view. The JSON shape is the contract in `builders-portal/lib/types.ts`. For the main-gate column on private repos the App needs `Checks: read`.

## Configuration

Runtime knobs live in `config.py`. Secrets (set as Railway variables, never in a member repo):

| variable | why |
| --- | --- |
| `ANTHROPIC_API_KEY` | the platform pays for reviews |
| `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` | fleet App — reads diffs, posts comments, reads the registry |
| `MODEL` | pinned review model (quality is fixed; quota is the cost dial) |
| `REVIEW_WEEKLY_DEFAULT`, `REVIEW_GLOBAL_WEEKLY_CAP`, `REVIEW_DIFF_LINE_CAP` | cost guards |
| `FLEET_ADMIN_KEY` | bearer for `/api/quota` and the admin view of `/api/fleet-status` (unset = `/api/quota` off, fleet-status member view only) |
| `FLEET_STATUS_TTL`, `FLEET_STATUS_ORIGINS` | snapshot lifetime in seconds (600) and the browser origins allowed to read it (builders portal + localhost:3101) |

The App needs access to `agent-registry` (to read the roster) plus each member repo (which members grant themselves).

## Local development

```bash
poetry install && poetry run pytest
```

The service boots without secrets — `/api/review` replies `not_configured` rather than crashing, so the endpoint can ship before its credentials do.

<!-- moved to the turingplanet org 2026-08-04 -->
