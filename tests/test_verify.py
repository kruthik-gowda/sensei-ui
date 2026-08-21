import subprocess

import pytest

from sensei_ui import verify
from sensei_ui.verify import adjudicate, apply_verdicts, extract_json


def _f(signature="inline|a.py|10"):
    return {"signature": signature, "file": "a.py", "line": 10,
            "kind": "must", "original_body": "b", "verdict": None,
            "verdict_reason": None}


def test_extract_json_handles_bare_object():
    assert extract_json('{"verdicts": []}') == {"verdicts": []}


def test_extract_json_strips_code_fence():
    raw = '```json\n{"verdicts": [], "missed": []}\n```'

    assert extract_json(raw) == {"verdicts": [], "missed": []}


def test_extract_json_ignores_surrounding_prose():
    raw = 'Here you go:\n{"verdicts": []}\nHope that helps.'

    assert extract_json(raw) == {"verdicts": []}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_verdicts_annotate_by_index():
    findings = [_f("sig-a"), _f("sig-b")]
    parsed = {"verdicts": [
        {"id": 0, "decision": "confirm", "reason": "real"},
        {"id": 1, "decision": "reject", "reason": "already handled"},
    ]}

    result = apply_verdicts(findings, parsed)

    assert result[0]["verdict"] == "confirm"
    assert result[1]["verdict"] == "reject"
    assert result[1]["verdict_reason"] == "already handled"


def test_missing_verdict_defaults_to_confirm():
    """Never silently drop a finding the model failed to rule on."""
    findings = [_f()]

    result = apply_verdicts(findings, {"verdicts": []})

    assert result[0]["verdict"] == "confirm"
    assert "no verdict" in result[0]["verdict_reason"]


def test_adjudication_never_removes_findings():
    findings = [_f("sig-a"), _f("sig-b")]
    parsed = {"verdicts": [{"id": 0, "decision": "reject", "reason": "x"},
                           {"id": 1, "decision": "reject", "reason": "y"}]}

    assert len(apply_verdicts(findings, parsed)) == 2


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_adjudicate_timeout_returns_findings_unannotated(monkeypatch):
    findings = [_f("sig-a"), _f("sig-b")]

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=verify.VERIFY_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    result, status = adjudicate(findings, "diff")

    assert status == "failed"
    assert len(result) == 2
    assert result == findings
    assert result[0]["verdict"] is None


def test_adjudicate_nonzero_returncode_returns_findings_unannotated(monkeypatch):
    findings = [_f("sig-a"), _f("sig-b")]

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=1, stdout="oops"),
    )

    result, status = adjudicate(findings, "diff")

    assert status == "failed"
    assert len(result) == 2
    assert result == findings
    assert result[0]["verdict"] is None


def test_adjudicate_unparseable_stdout_returns_findings_unannotated(monkeypatch):
    findings = [_f("sig-a"), _f("sig-b")]

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="no json here"),
    )

    result, status = adjudicate(findings, "diff")

    assert status == "failed"
    assert len(result) == 2
    assert result == findings
    assert result[0]["verdict"] is None


def test_adjudicate_well_formed_response_annotates_findings(monkeypatch):
    findings = [_f("sig-a"), _f("sig-b")]
    stdout = (
        '{"verdicts":[{"id":0,"decision":"confirm","reason":"real"},'
        '{"id":1,"decision":"reject","reason":"handled"}]}'
    )

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout=stdout),
    )

    result, status = adjudicate(findings, "diff")

    assert status == "ok"
    assert len(result) == 2
    assert result[0]["verdict"] == "confirm"
    assert result[1]["verdict"] == "reject"


def test_adjudicate_empty_findings_skips_subprocess(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run should not be invoked for empty findings")

    monkeypatch.setattr(subprocess, "run", _fail_if_called)

    result, status = adjudicate([], "diff")

    assert result == []
    assert status == "skipped"


def test_adjudicate_short_diff_has_no_truncation_notice(monkeypatch):
    captured = {}

    def _capture(*args, **kwargs):
        captured["prompt"] = kwargs.get("input")
        return _FakeCompletedProcess(returncode=0, stdout='{"verdicts": []}')

    monkeypatch.setattr(subprocess, "run", _capture)

    adjudicate([_f()], "short diff")

    assert "truncated" not in captured["prompt"]


def test_adjudicate_long_diff_has_truncation_notice(monkeypatch):
    captured = {}

    def _capture(*args, **kwargs):
        captured["prompt"] = kwargs.get("input")
        return _FakeCompletedProcess(returncode=0, stdout='{"verdicts": []}')

    monkeypatch.setattr(subprocess, "run", _capture)

    long_diff = "x" * (verify.MAX_DIFF_CHARS + 1)
    adjudicate([_f()], long_diff)

    assert "truncated" in captured["prompt"]
