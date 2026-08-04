import config
from api.server import run, say_hi
from mcp_server.server import tool_run, tool_say_hi


def test_config_defaults_are_sane():
    # config.py is the single source of runtime configuration.
    assert config.MCP_TRANSPORT in ("stdio", "streamable-http")
    assert isinstance(config.PORT, int)


def test_run_returns_a_string():
    # Placeholder smoke test so the pipeline is green out of the box.
    # Replace this with real tests for your agent.
    assert isinstance(run("ping"), str)


def test_say_hi_format():
    # "hello from <timezone and current time>: hi"
    msg = say_hi()
    assert msg.startswith("hello from ")
    assert msg.endswith(": hi")


def test_mcp_tools_delegate_to_api():
    assert tool_run("ping") == run("ping")
    assert tool_say_hi().endswith(": hi")
