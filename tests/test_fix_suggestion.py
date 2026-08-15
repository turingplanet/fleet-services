from api.review import PERSONAS, _is_fix, _parse_type, help_text


def test_is_fix_variants():
    assert _is_fix("/fix-suggestion")
    assert _is_fix("/fix-suggestion please")
    assert _is_fix("/review fix")
    assert _is_fix("/review fix ")
    assert not _is_fix("/review")
    assert not _is_fix("/review security")
    assert not _is_fix("/review fixation")   # word boundary


def test_fix_persona_and_help():
    assert "fix" in PERSONAS
    assert "/fix-suggestion" in help_text(0, 5)
    # plain /review parsing is untouched
    assert _parse_type("/review") == "security"


def test_usage_line_and_footer():
    from types import SimpleNamespace

    from api.review import MARKER, _footer, _usage_line

    u = SimpleNamespace(input_tokens=10_000, output_tokens=2_000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    line = _usage_line(u)
    assert "10,000 in / 2,000 out" in line
    assert "$" in line  # claude-opus-4-8 is priced in the table
    # opus rates: 10k * $5/M + 2k * $25/M = $0.05 + $0.05 = $0.10
    assert "0.1000" in line
    assert _usage_line(None) == ""
    footer = _footer(1, 5, u)
    assert MARKER in footer and "1/5" in footer and "10,000" in footer
