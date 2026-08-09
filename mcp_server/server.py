"""/mcp_server — one process, two surfaces over the same /api logic.

  • MCP (for Claude)      → /mcp   (stdio locally; Streamable HTTP when deployed)
  • REST API (for humans) → /api   (FastAPI; only served in HTTP/deploy mode)

All knobs live in ONE file: config.py.

Local (stdio, MCP only):   poetry run python mcp_server/server.py
Connect Claude:            claude mcp add fleet-services -- poetry -C "$(pwd)" run python "$(pwd)/mcp_server/server.py"
Deployed (HTTP, both):     platforms inject PORT → serves /mcp AND /api. Connect Claude with
                           claude mcp add --transport http fleet-services https://<your-app>/mcp
                           and hit the REST API at   https://<your-app>/api/say_hi

(The folder is named mcp_server, NOT mcp, on purpose: a local `mcp/` package
would collide with the installed `mcp` SDK and break imports.)
"""
import sys
from pathlib import Path

# Make /api and config.py importable no matter where the server is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

import config
from api.server import run, say_hi

# --- MCP surface (tools) -------------------------------------------------
mcp = FastMCP(config.AGENT_NAME, host=config.HOST, port=config.PORT)


@mcp.tool()
def tool_say_hi() -> str:
    """Say hi, prefixed with this MCP server's timezone and current time."""
    return say_hi()


@mcp.tool()
def tool_run(payload: str = "ping") -> str:
    """Placeholder tool; delegates to your business logic in /api."""
    return run(payload)


# --- combined ASGI app: MCP at /mcp + REST at /api (HTTP/deploy mode) -----
def build_http_app():
    """FastAPI app serving the REST API, with the MCP server mounted at /mcp.

    The MCP session manager must run inside the app lifespan — a mounted
    sub-app's own lifespan is NOT started by the parent, so we start it here.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    mcp_app = mcp.streamable_http_app()  # Starlette app that serves /mcp

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title=f"{config.AGENT_NAME} API", lifespan=lifespan)

    # Your REST endpoints — add more as you grow /api. Same logic the tools use.
    @app.get("/api/say_hi")
    def api_say_hi():
        return {"message": say_hi()}

    @app.get("/api/run")
    def api_run(payload: str = "ping"):
        return {"result": run(payload)}

    @app.get("/api/health")
    def api_health():
        return {"ok": True, "agent": config.AGENT_NAME}

    # --- platform AI review (RFC 002) — advisory only, quota via registry ---
    from fastapi import BackgroundTasks
    from pydantic import BaseModel

    class ReviewRequest(BaseModel):
        repo: str
        pr_number: int
        comment_id: int

    @app.post("/api/review", status_code=202)
    def api_review(req: ReviewRequest, background: BackgroundTasks):
        import os

        if not (os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY")):
            return {"status": "not_configured"}
        from api.review import handle_review

        background.add_task(handle_review, req.repo, req.pr_number, req.comment_id)
        return {"status": "accepted"}

    # --- self-service fleet registration (RFC 001 §10 / M4) -----------------
    class RegisterRequest(BaseModel):
        repo: str

    @app.post("/api/register")
    def api_register(req: RegisterRequest):
        import os

        if not (os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY")):
            return {"status": "not_configured"}
        from api.registrar import handle_register

        return handle_register(req.repo)

    @app.post("/api/deregister")
    def api_deregister(req: RegisterRequest):
        import os

        if not (os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY")):
            return {"status": "not_configured"}
        from api.registrar import handle_deregister

        return handle_deregister(req.repo)

    app.mount("/", mcp_app)  # /mcp is served by the mounted MCP app
    return app


if __name__ == "__main__":
    if config.MCP_TRANSPORT == "stdio":
        mcp.run(transport="stdio")          # local: Claude over stdin/stdout (no HTTP, no /api)
    else:
        import uvicorn

        uvicorn.run(build_http_app(), host=config.HOST, port=config.PORT)  # deployed: /mcp + /api
