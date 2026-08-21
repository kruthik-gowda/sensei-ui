import pytest

from sensei_ui.verify import apply_verdicts, extract_json


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
