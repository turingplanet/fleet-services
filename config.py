"""THE ONE CONFIG FILE for this agent.

Every runtime knob lives here, and every value can be overridden by an
environment variable of the same name — so "configuring a deployment"
always means the same thing: look at this file, set the env vars you
need on your platform, done. Changing model or deployment platform
later should only require reading this file.

=== SECRETS / ENV-VAR CHECKLIST (what to set on your platform) ===============
| Env var           | Needed when…                          | Default        |
|-------------------|---------------------------------------|----------------|
| PORT              | deploying remotely — most platforms   | 8000           |
|                   | (Railway/Render/Fly) inject it, which |                |
|                   | automatically switches to HTTP mode   |                |
| MCP_TRANSPORT     | you want to force a transport         | auto (see below)|
| HOST              | rarely — bind address for HTTP mode   | 0.0.0.0        |
| MODEL             | your /api code calls an LLM           | claude-opus-4-8|
| ANTHROPIC_API_KEY | your /api code calls Claude           | (none)         |
| FLEET_ADMIN_KEY   | admin-only read endpoints (/api/quota)| (none = off)   |
|                   | and the admin view of /api/fleet-status|               |
| FLEET_STATUS_TTL  | how long a fleet snapshot is served   | 600 (seconds)  |
|                   | before it is rebuilt in the background|                |
| FLEET_STATUS_ORIGINS | browser origins allowed to read    | builders portal|
|                   | /api/fleet-status (comma-separated)   | + localhost:3101|
==============================================================================
The starter agent needs NONE of these locally — stdio + no secrets.
"""
import os

# --- identity -----------------------------------------------------------
AGENT_NAME = "fleet-services"

# --- MCP transport / deployment ------------------------------------------
# stdio            → local: Claude launches this process itself (the default)
# streamable-http  → remote: the server listens on HOST:PORT at /mcp
# Auto-detection: deployment platforms inject PORT, so "PORT is set" means
# "we're deployed" — override explicitly with MCP_TRANSPORT if needed.
MCP_TRANSPORT = os.environ.get(
    "MCP_TRANSPORT",
    "streamable-http" if os.environ.get("PORT") else "stdio",
)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# --- model (only used if/when your /api logic calls an LLM) ---------------
MODEL = os.environ.get("MODEL", "claude-opus-4-8")

# --- admin (read-only status endpoints; empty = those endpoints are disabled) --
FLEET_ADMIN_KEY = os.environ.get("FLEET_ADMIN_KEY", "")

# --- fleet status (GET /api/fleet-status, read by the builders portal) -----
FLEET_STATUS_TTL = int(os.environ.get("FLEET_STATUS_TTL", "600"))
FLEET_STATUS_ORIGINS = {
    o.strip().rstrip("/")
    for o in os.environ.get(
        "FLEET_STATUS_ORIGINS", "https://builders.turingplanet.ai,http://localhost:3101"
    ).split(",")
    if o.strip()
}
GATEWAY_URL = os.environ.get("GATEWAY_URL", "https://mcp.agents.turingplanet.ai").rstrip("/")
FLEET_DOMAIN = os.environ.get("FLEET_DOMAIN", "agents.turingplanet.ai")
TEMPLATE_REPO = os.environ.get("TEMPLATE_REPO", "turingplanet/agent-template")
POLICIES_REPO = os.environ.get("POLICIES_REPO", "turingplanet/policies")
# A check run counts as the gate when its name contains this (policies' job is "review / review").
GATE_CHECK_NAME = os.environ.get("GATE_CHECK_NAME", "review").lower()
