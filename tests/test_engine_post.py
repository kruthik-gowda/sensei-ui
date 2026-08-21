from sensei.gitlab_client import build_body_signature

from sensei_ui.engine import _deserialise_signature, _serialise_signature, plan_posts


def _f(signature, file="a.py", line=10, kind="must", body="text"):
    return {
        "signature": signature, "file": file, "line": line,
        "kind": kind, "original_body": body, "edited_body": None,
    }


def test_must_finding_on_diff_line_goes_inline():
    plan = plan_posts([_f("inline|a.py|10")], {"a.py": {10}}, set())

    assert len(plan["inline"]) == 1
    assert plan["summary"] == []
    assert plan["skipped"] == []


def test_must_finding_off_diff_is_demoted_to_summary():
    """Never relocate an anchor — demote it, visibly."""
    plan = plan_posts([_f("inline|a.py|99", line=99)], {"a.py": {10}}, set())

    assert plan["inline"] == []
    assert len(plan["summary"]) == 1


def test_nit_always_goes_to_summary():
    plan = plan_posts([_f("body|x", kind="nit", line=None)], {"a.py": {10}}, set())

    assert plan["inline"] == []
    assert len(plan["summary"]) == 1


def test_already_posted_signature_is_skipped():
    existing = {("inline", "a.py", 10)}
    plan = plan_posts([_f("inline|a.py|10")], {"a.py": {10}}, existing)

    assert plan["inline"] == []
    assert len(plan["skipped"]) == 1


def test_edited_body_wins_over_original():
    finding = _f("inline|a.py|10")
    finding["edited_body"] = "my better wording"

    plan = plan_posts([finding], {"a.py": {10}}, set())

    assert plan["inline"][0]["body"] == "my better wording"


def test_unedited_finding_posts_original_body():
    plan = plan_posts([_f("inline|a.py|10", body="original")], {"a.py": {10}}, set())

    assert plan["inline"][0]["body"] == "original"


def test_body_signature_with_pipe_characters_round_trips():
    original = build_body_signature("| col1 | col2 |\n> quoted | diff | line")

    serialised = _serialise_signature(original)
    deserialised = _deserialise_signature(serialised)

    assert deserialised == original
