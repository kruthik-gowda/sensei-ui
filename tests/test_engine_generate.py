import subprocess

import pytest

from sensei_ui.engine import EngineError, generate, snapshot_path, to_findings


def test_snapshot_path_matches_sensei_naming():
    path = snapshot_path("group/sub/proj", 42)

    assert path.endswith("group_sub_proj_42.json")
    assert ".sensei/reviews" in path


def test_inline_finding_gets_inline_signature():
    findings = to_findings(
        [{"file": "a.py", "line": 10, "type": "must",
          "confidence": 95, "body": "problem here"}]
    )

    assert findings[0]["signature"] == "inline|a.py|10"
    assert findings[0]["kind"] == "must"
    assert findings[0]["original_body"] == "problem here"


def test_finding_without_line_gets_body_signature():
    findings = to_findings(
        [{"file": "a.py", "line": 0, "type": "nit",
          "confidence": 85, "body": "  spaced   out  "}]
    )

    assert findings[0]["signature"] == "body|spaced out"


def test_signatures_are_stored_as_strings():
    """SQLite cannot store a tuple; the store column is TEXT."""
    findings = to_findings(
        [{"file": "a.py", "line": 10, "type": "must",
          "confidence": 95, "body": "x"}]
    )

    assert isinstance(findings[0]["signature"], str)


def test_empty_comment_list_yields_no_findings():
    assert to_findings([]) == []


def test_generate_raises_engine_error_on_timeout(monkeypatch):
    """A hung `sensei review` must surface as a clean 502, not a bare 500."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sensei", timeout=900)

    monkeypatch.setattr("sensei_ui.engine.subprocess.run", fake_run)

    with pytest.raises(EngineError, match="timed out"):
        generate("https://gitlab.example.com/g/p/-/merge_requests/7")
