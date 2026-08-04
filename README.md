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

Reviews never block a PR — the member's own gate decides. Help and decline replies carry a different marker so they never consume quota. Any unexpected failure is reported on the PR: silent failure is banned.

**Planned:** `POST /api/register` — the deferred auto-registration endpoint (RFC 001 §10).

## Configuration

Runtime knobs live in `config.py`. Secrets (set as Railway variables, never in a member repo):

| variable | why |
| --- | --- |
| `ANTHROPIC_API_KEY` | the platform pays for reviews |
| `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` | fleet App — reads diffs, posts comments, reads the registry |
| `MODEL` | pinned review model (quality is fixed; quota is the cost dial) |
| `REVIEW_WEEKLY_DEFAULT`, `REVIEW_GLOBAL_WEEKLY_CAP`, `REVIEW_DIFF_LINE_CAP` | cost guards |

The App needs access to `agent-registry` (to read the roster) plus each member repo (which members grant themselves).

## Local development

```bash
poetry install && poetry run pytest
```

The service boots without secrets — `/api/review` replies `not_configured` rather than crashing, so the endpoint can ship before its credentials do.

<!-- moved to the turingplanet org 2026-08-04 -->
