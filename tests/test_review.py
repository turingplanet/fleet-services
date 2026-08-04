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
