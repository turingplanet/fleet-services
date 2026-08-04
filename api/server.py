"""/api — your business logic. The ONLY place a contributor writes real code.

`say_hi()` is a small working example (exposed by the MCP server in /mcp_server).
Replace the placeholder `run()` below with your agent's real logic.
"""
from datetime import datetime


def say_hi() -> str:
    """Working example: greet with the server's timezone and current time."""
    now = datetime.now().astimezone()
    tz = now.tzname() or "unknown timezone"
    return f"hello from {tz} {now:%Y-%m-%d %H:%M:%S}: hi"


def run(payload: str = "ping") -> str:
    """Placeholder entrypoint — replace with your agent's real logic."""
    return f"TODO: implement your agent. You sent: {payload}"
