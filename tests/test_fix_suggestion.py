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
