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
    """Parse a unified diff into rows. Returns [] for empty input.

    Expects a SINGLE file's diff text (e.g. GitLab's per-file `diff` string),
    not a whole multi-file patch. Rows carry no filename, so multi-file input
    produces rows that cannot be attributed back to a specific file.
    """
    if not diff_text or not diff_text.strip():
        return []

    text = diff_text
    if not text.lstrip().startswith("---"):
        # GitLab's per-file `diff` field is hunks only, with no `---`/`+++`
        # file header lines; unidiff requires one to anchor a PatchedFile.
        text = "--- a/file\n+++ b/file\n" + text

    patch = PatchSet(text)
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
                if (
                    not line.is_added
                    and not line.is_removed
                    and line.source_line_no is None
                    and line.target_line_no is None
                ):
                    continue

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
