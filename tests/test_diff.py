from sensei_ui.diff import parse_diff

SIMPLE = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-old_value = 1
+new_value = 2
+extra = 3
 done = True
"""

MULTI_HUNK = """--- a/m.py
+++ b/m.py
@@ -1,2 +1,2 @@
 first
-second
+SECOND
@@ -10,2 +10,2 @@
 tenth
-eleventh
+ELEVENTH
"""

NEW_FILE = """--- /dev/null
+++ b/brand_new.py
@@ -0,0 +1,2 @@
+line one
+line two
"""


def test_context_line_carries_both_line_numbers():
    rows = parse_diff(SIMPLE)
    context = [r for r in rows if r["kind"] == "context"]

    assert context[0]["text"] == "import os"
    assert context[0]["old_line"] == 1
    assert context[0]["new_line"] == 1


def test_deletion_has_old_line_only():
    rows = parse_diff(SIMPLE)
    deleted = [r for r in rows if r["kind"] == "del"]

    assert len(deleted) == 1
    assert deleted[0]["text"] == "old_value = 1"
    assert deleted[0]["old_line"] == 2
    assert deleted[0]["new_line"] is None


def test_addition_has_new_line_only():
    rows = parse_diff(SIMPLE)
    added = [r for r in rows if r["kind"] == "add"]

    assert [r["text"] for r in added] == ["new_value = 2", "extra = 3"]
    assert [r["new_line"] for r in added] == [2, 3]
    assert all(r["old_line"] is None for r in added)


def test_second_hunk_resumes_at_its_header_line_numbers():
    rows = parse_diff(MULTI_HUNK)
    added = [r for r in rows if r["kind"] == "add"]

    assert added[0]["new_line"] == 2
    assert added[1]["new_line"] == 11


def test_hunk_header_emitted_as_meta_row():
    rows = parse_diff(MULTI_HUNK)
    meta = [r for r in rows if r["kind"] == "meta"]

    assert len(meta) == 2
    assert meta[0]["text"].startswith("@@")


def test_new_file_produces_only_additions():
    rows = parse_diff(NEW_FILE)
    kinds = {r["kind"] for r in rows}

    assert "del" not in kinds
    assert [r["new_line"] for r in rows if r["kind"] == "add"] == [1, 2]


def test_empty_diff_returns_no_rows():
    assert parse_diff("") == []


def test_new_line_numbers_agree_with_sensei_extract_diff_lines():
    """Anchor validation and rendering must not disagree about line numbers."""
    from sensei.gitlab_client import extract_diff_lines

    rows = parse_diff(SIMPLE)
    ours = {r["new_line"] for r in rows if r["kind"] == "add"}

    assert ours == extract_diff_lines(SIMPLE)
