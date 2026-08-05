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
