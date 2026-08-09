from api.review import _changed_lines, _parse_type


def test_parse_type_defaults_to_security():
    assert _parse_type("/review") == "security"
    assert _parse_type("/review   ") == "security"


def test_parse_type_variants():
    assert _parse_type("/review perf") == "perf"
    assert _parse_type("/review GENERAL please") == "general"
    assert _parse_type("/review nonsense") is None


def test_changed_lines_ignores_file_headers():
    diff = "--- a/x.py\n+++ b/x.py\n@@\n+added\n-removed\n context\n"
    assert _changed_lines(diff) == 2


def test_parse_type_help():
    from api.review import _parse_type
    assert _parse_type("/review help") == "help"


def test_help_text_contextual():
    from api.review import help_text
    disabled = help_text(0, 0)
    assert "not enabled" in disabled and "members.yaml" in disabled
    enabled = help_text(2, 5)
    assert "/review perf" in enabled and "2/5" in enabled


def test_markers_are_distinct():
    """Declines and help must not count against quota."""
    from api.review import MARKER, MARKER_HELP
    assert MARKER != MARKER_HELP and MARKER not in MARKER_HELP


def test_register_reply_wordings():
    from api.registrar import register_reply
    assert "Registration PR opened" in register_reply({"status": "pr_opened", "pr": "http://x"})
    assert "already on the fleet roster" in register_reply({"status": "already_registered"})
    assert "install the platform App" in register_reply(
        {"status": "app_not_installed", "install_url": "http://x"})
    assert "fleet.register" in register_reply({"status": "not_consented"})


def test_register_repo_validation():
    from api.registrar import handle_register
    assert handle_register("not-a-repo")["status"] == "invalid_repo"
    assert handle_register("../evil/path")["status"] == "invalid_repo"


SAMPLE_MEMBERS = """# header comment
members:
  - name: hello-agent
    repo: enochhz/hello-agent

  - name: bye-agent
    repo: alice/bye-agent
    ai_review:
      weekly_limit: 5   # pilot

  - name: keeper
    repo: bob/keeper
"""

SAMPLE_DEPLOYS = """# header
deployments:
  - slug: hello-fleet
    repo: enochhz/hello-fleet
    host: platform
  - slug: bye-agent
    repo: alice/bye-agent
    host: platform
"""


def test_remove_entry_with_subkeys():
    from api.registrar import _remove_entry
    out = _remove_entry(SAMPLE_MEMBERS, "alice/bye-agent")
    assert "bye-agent" not in out and "weekly_limit" not in out
    assert "hello-agent" in out and "keeper" in out
    import yaml
    assert len(yaml.safe_load(out)["members"]) == 2


def test_remove_entry_deployments():
    from api.registrar import _remove_entry
    out = _remove_entry(SAMPLE_DEPLOYS, "alice/bye-agent")
    assert "bye-agent" not in out and "hello-fleet" in out
    import yaml
    assert len(yaml.safe_load(out)["deployments"]) == 1


def test_remove_entry_noop_when_absent():
    from api.registrar import _remove_entry
    assert _remove_entry(SAMPLE_MEMBERS, "nobody/nothing") == SAMPLE_MEMBERS


def test_deregister_repo_validation():
    from api.registrar import handle_deregister
    assert handle_deregister("junk")["status"] == "invalid_repo"
