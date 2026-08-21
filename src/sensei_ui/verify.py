"""Claude adjudication of generated findings.

This pass annotates; it never drops. A rejected finding still reaches the
reviewer, marked and with its reasoning attached, because a wrong rejection
must remain recoverable.
"""
import json
import re
import subprocess
from typing import Dict, List, Tuple

VERIFY_TIMEOUT = 900
MAX_DIFF_CHARS = 120000

PROMPT = """You are adjudicating automated code-review findings before they are \
posted to a colleague's merge request. A wrong finding wastes their time, so \
reject anything you cannot substantiate from the diff.

For EACH finding decide:
  - "confirm" - the diff clearly shows the problem is real
  - "reject"  - wrong, already handled in the diff, or unsupported by the code

Bias toward reject when uncertain.

Return ONLY minified JSON, no prose, no code fence:
{"verdicts":[{"id":<int>,"decision":"confirm"|"reject","reason":"<short>"}]}

## Findings
%(findings)s

## Diff
%(diff)s
"""


def extract_json(raw: str) -> Dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output: %s" % raw[:200])
    return json.loads(cleaned[start:end + 1])


def apply_verdicts(findings: List[Dict], parsed: Dict) -> List[Dict]:
    decisions = {}
    for verdict in parsed.get("verdicts", []):
        try:
            decisions[int(verdict["id"])] = (
                verdict.get("decision"),
                verdict.get("reason", ""),
            )
        except (KeyError, TypeError, ValueError):
            continue

    annotated = []
    for index, finding in enumerate(findings):
        decision, reason = decisions.get(
            index, ("confirm", "no verdict returned")
        )
        entry = dict(finding)
        entry["verdict"] = "reject" if decision == "reject" else "confirm"
        entry["verdict_reason"] = reason
        annotated.append(entry)

    return annotated


def adjudicate(findings: List[Dict], diff_text: str) -> Tuple[List[Dict], str]:
    """Returns (annotated findings, verify_status)."""
    if not findings:
        return [], "skipped"

    listing = "\n\n".join(
        "[%d] %s:%s\n%s"
        % (i, f["file"], f.get("line"), f["original_body"].strip())
        for i, f in enumerate(findings)
    )
    prompt = PROMPT % {"findings": listing, "diff": diff_text[:MAX_DIFF_CHARS]}

    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return findings, "failed"

    if proc.returncode != 0:
        return findings, "failed"

    try:
        parsed = extract_json(proc.stdout)
    except ValueError:
        return findings, "failed"

    return apply_verdicts(findings, parsed), "ok"
