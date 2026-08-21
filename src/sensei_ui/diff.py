"""Unified-diff parsing into explicit side-by-side rows.

Line mapping decides which line a review comment anchors to, so it is computed
once here — server-side and unit-tested — and consumed unchanged by both the
renderer and anchor validation.
"""
from typing import Dict, List, Optional

from unidiff import PatchSet


def _row(
    kind: str,
    text: str,
    old_line: Optional[int] = None,
    new_line: Optional[int] = None,
) -> Dict:
    return {
        "kind": kind,
        "old_line": old_line,
        "new_line": new_line,
        "text": text,
    }


def parse_diff(diff_text: str) -> List[Dict]:
    """Parse a unified diff into rows. Returns [] for empty input."""
    if not diff_text or not diff_text.strip():
        return []

    patch = PatchSet(diff_text)
    rows: List[Dict] = []

    for patched_file in patch:
        for hunk in patched_file:
            header = "@@ -%d,%d +%d,%d @@" % (
                hunk.source_start,
                hunk.source_length,
                hunk.target_start,
                hunk.target_length,
            )
            rows.append(_row("meta", header))
            for line in hunk:
                text = line.value.rstrip("\n")
                if line.is_added:
                    rows.append(_row("add", text, None, line.target_line_no))
                elif line.is_removed:
                    rows.append(_row("del", text, line.source_line_no, None))
                else:
                    rows.append(
                        _row(
                            "context",
                            text,
                            line.source_line_no,
                            line.target_line_no,
                        )
                    )

    return rows
