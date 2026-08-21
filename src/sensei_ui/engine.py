"""The only module permitted to import from sensei.

Everything here composes sensei's *public* API. Its private orchestration
(`cli._post_review_results`) is deliberately not used: it discards the created
discussion id, which undo and partial-failure recovery both require.
"""
import json
import os
import subprocess
from typing import Dict, List, Optional

from sensei.config import load_config
from sensei.gitlab_client import (
    build_body_signature,
    build_inline_signature,
)
from sensei.review_platform import parse_review_url

REVIEWS_DIR = os.path.expanduser("~/.sensei/reviews")
GENERATE_TIMEOUT = 900


class EngineError(RuntimeError):
    """Raised when sensei cannot produce a review."""


def snapshot_path(project_path: str, mr_iid: int) -> str:
    return os.path.join(
        REVIEWS_DIR, "%s_%d.json" % (project_path.replace("/", "_"), mr_iid)
    )


def _serialise_signature(signature_tuple) -> str:
    return "|".join(str(part) for part in signature_tuple)


def to_findings(comments: List[Dict]) -> List[Dict]:
    """Convert sensei snapshot comments into store-shaped findings."""
    findings = []
    for c in comments:
        line = c.get("line") or 0
        if line > 0:
            signature = build_inline_signature(c["file"], line)
        else:
            signature = build_body_signature(c.get("body", ""))
        findings.append(
            {
                "signature": _serialise_signature(signature),
                "file": c["file"],
                "line": line or None,
                "kind": c.get("type", "nit"),
                "confidence": c.get("confidence"),
                "original_body": c.get("body", ""),
                "verdict": None,
                "verdict_reason": None,
            }
        )
    return findings


def generate(mr_url: str) -> Dict:
    """Run sensei in dry-run mode and load the snapshot it writes."""
    proc = subprocess.run(
        ["sensei", "review", mr_url, "--dry-run", "--fresh"],
        capture_output=True,
        text=True,
        timeout=GENERATE_TIMEOUT,
    )
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-500:]
        raise EngineError("sensei review failed: %s" % tail)

    target = parse_review_url(mr_url)
    project_path = str(target["project_path"])
    mr_iid = int(target["review_id"])

    path = snapshot_path(project_path, mr_iid)
    if not os.path.exists(path):
        raise EngineError("snapshot not written at %s" % path)

    with open(path) as fh:
        snapshot = json.load(fh)

    client = make_client(mr_url)
    mr_data = client.get_mr_diff(project_path, mr_iid)
    # get_mr_diff returns the shas flat; rebuild the nested shape posting wants.
    diff_refs = {
        "base_sha": mr_data["base_sha"],
        "head_sha": mr_data["head_sha"],
        "start_sha": mr_data["start_sha"],
    }

    return {
        "project_path": project_path,
        "mr_iid": mr_iid,
        "head_sha": mr_data["head_sha"],
        "findings": to_findings(snapshot.get("comments", [])),
        "test_summary": snapshot.get("test_summary"),
        "files": mr_data.get("files", []),
        "diff_refs": diff_refs,
    }


def make_client(mr_url: str):
    """Build a sensei GitLab client for this MR's host."""
    from sensei.gitlab_client import GitLabClient

    config = load_config()
    parse_review_url(mr_url)
    return GitLabClient(config["gitlab_url"], config["gitlab_pat"])
