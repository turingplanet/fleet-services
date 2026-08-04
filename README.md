# fleet-services

Platform-side services: on-demand AI review (RFC 002), registrar (RFC 001 §10)

A member agent of **图灵星球 Agent 军团**, generated from [agent-template](https://github.com/turingplanet/agent-template) with [Copier](https://copier.readthedocs.io). Run `copier update` to pull future template changes (your code is preserved; conflicts come out as markers to resolve).

## Setup checklist
1. **Install & run locally** → [Run the MCP server](#run-the-mcp-server--connect-claude) (`poetry install`, connect Claude).
2. **Push to GitHub as its own repo** — run from *inside this folder* so the repo root is the agent:
   ```bash
   git init && git add -A && git commit -m "Scaffold from agent-template"
   gh repo create fleet-services --private --source . --push
   ```
   (If your deploy later says *"root only contains subdirectories"*, you pushed a parent folder — redo this from inside the agent folder.)
3. **Fleet auto-sync** (optional but recommended) → [grant the bot access](#fleet-auto-sync-keep-this-repo-on-the-latest-template).
4. **Deploy** (optional) → [Deploy remotely](#deploy-remotely-connect-from-anywhere).

## Layout
- `agent.manifest.yaml` — the instruction card: toolchain, paths, and commands.
- **`config.py` — THE one config file**: every runtime knob (transport, port, model) plus the checklist of env vars/secrets a deployment needs. Changing model or platform later = read this one file.
- `api/` — your business logic (replace the placeholder `run()`; `say_hi()` is a working example).
- `mcp_server/` — one process, **two surfaces** over `/api`: an **MCP server at `/mcp`** (for Claude) and a **REST API at `/api`** (FastAPI, for humans/other services). Local runs use stdio (MCP only); deployed runs serve both over HTTP.
- `tests/` — smoke tests.
- `.github/workflows/review.yml` — thin pointer to the central review flow.

## Run the MCP server & connect Claude

```bash
poetry install                                # once
# register with Claude (run from the repo root; stores absolute paths):
claude mcp add fleet-services -- poetry -C "$(pwd)" run python "$(pwd)/mcp_server/server.py"
```

Then in Claude, ask it to call the **`tool_say_hi`** tool — it replies with this server's timezone and current time:

```
hello from PDT 2026-07-03 15:04:05: hi
```

Add your own tools by writing functions in `api/` and exposing them with `@mcp.tool()` in `mcp_server/server.py`.

## Deploy remotely (connect from anywhere)

The same server switches to **HTTP mode automatically when the platform injects a `PORT`** (Railway, Render, Fly.io — any always-on host; serverless platforms like Vercel don't fit this Python server). No code change needed:

1. Make sure **`poetry.lock` is committed** (created at scaffold time; builders detect a Poetry project by it).
2. Push this repo to GitHub and create a project on your platform (e.g. Railway → *Deploy from GitHub repo*). The start command ships in **`railpack.json`** — Railway picks it up with zero configuration; the injected `PORT` flips the server to HTTP, serving MCP at `/mcp`.
3. Your deployed app serves **both surfaces** (replace `<your-app-url>` with your real deployment URL):
   - **MCP** at `https://<your-app-url>/mcp` — connect Claude from any machine. The `-cloud` suffix keeps this remote registration separate from your local stdio one (same server name would clash):
     ```bash
     claude mcp add --transport http --scope user fleet-services-cloud https://<your-app-url>/mcp
     ```
     Then in a new Claude session: `/mcp` shows `fleet-services-cloud` connected → ask it to call `tool_say_hi` → the time comes back in the **server's** timezone (e.g. UTC on Railway), proof it's the remote one.
   - **REST API** at `https://<your-app-url>/api/...` — for humans, scripts, or other services:
     ```bash
     curl https://<your-app-url>/api/say_hi      # {"message":"hello from UTC …: hi"}
     ```
     Add more endpoints in `mcp_server/server.py` (`build_http_app`), reusing your `/api` logic.

Everything configurable about the deployment (transport, port, model, which secrets to set) is documented in **`config.py`** — that's the only file to read when you change platform or model.

> ⚠️ A deployed server is public: anyone with the URL can call your tools. Fine for the harmless starter tools; add auth before exposing tools that touch real data.

## Fleet auto-sync (keep this repo on the latest template)

This agent can be tracked by the [fleet migration bot](https://github.com/turingplanet/agent-registry): when a new `agent-template` version ships, the bot opens a PR here bumping you to it (you review + merge — never auto-merged). **Two** things must be true:

1. **You're listed in the fleet's `members.yaml`.** The scaffold offers to open that PR for you (the first Copier question). If you skipped it or lacked registry access, ask the platform admin to add:
   ```yaml
   - name: fleet-services
     repo: <owner>/fleet-services
   ```
2. **The bot's GitHub App can access this repo.** ⚠️ *Registration alone is NOT enough* — a GitHub App can't grant itself access; the **owner of this repo's account** grants it once:
   - GitHub → **Settings → Applications → Installed GitHub Apps → `fleet-migration-bot` → Configure**
   - Under **Repository access**: add this repo, **or** choose **All repositories** (simplest for a personal account — the bot only ever touches repos in `members.yaml`).
   - On an org you don't administer, ask the platform admin to grant it.

> If a sync run fails with **"Not Found"** on your repo, it's always #2 — the App hasn't been granted access yet.

## How review works
Open a pull request → the review flow from [`policies`](https://github.com/turingplanet/policies) reads the manifest, installs, runs the tests, lints, scans for security issues, lets the AI reviewer advise — and the **gate** (the hard checks) decides pass/fail. See the [platform overview](https://github.com/turingplanet/agent-legion) for the full picture.
