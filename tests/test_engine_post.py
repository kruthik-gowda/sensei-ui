from sensei.gitlab_client import build_body_signature

from sensei_ui.engine import build_storage_signature, dedup_key_for, plan_posts


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


def test_body_signature_with_pipe_characters_still_dedupes():
    """A markdown table body is full of `|`; the remote key must survive it.

    The dedup key is now derived from the finding's own fields rather than
    parsed out of the stored signature, so this asserts the property the old
    round-trip test protected: a pipe-heavy body GitLab already carries is
    recognised and skipped.
    """
    body = "| col1 | col2 |\n> quoted | diff | line"
    finding = _f(
        build_storage_signature(build_body_signature(body), body),
        kind="nit", line=None, body=body,
    )

    assert dedup_key_for(finding) == build_body_signature(body)

    plan = plan_posts([finding], {}, {build_body_signature(body)})

    assert plan["summary"] == []
    assert len(plan["skipped"]) == 1


def test_two_findings_on_one_line_get_distinct_storage_signatures():
    """Storage identity is per finding; remote identity is per anchor."""
    key = ("inline", "a.py", 42)

    first = build_storage_signature(key, "first problem")
    second = build_storage_signature(key, "second problem")

    assert first != second
    assert first.startswith("inline|a.py|42#")
    assert second.startswith("inline|a.py|42#")


def test_storage_signature_is_stable_across_whitespace_churn():
    """Reformatting regenerated text must not orphan a reviewer decision."""
    key = ("inline", "a.py", 42)

    assert build_storage_signature(key, "same  words   here") == (
        build_storage_signature(key, "same words here")
    )


def test_dedup_key_for_inline_finding_ignores_body():
    finding = _f("inline|a.py|10#deadbeef", body="anything at all")

    assert dedup_key_for(finding) == ("inline", "a.py", 10)


class _FakeNotes:
    def __init__(self):
        self.deleted = []

    def delete(self, note_id):
        self.deleted.append(note_id)


class _FakeDiscussion:
    def __init__(self, discussion_id):
        self.id = discussion_id
        self.attributes = {"notes": [{"id": 1}]}
        self.notes = _FakeNotes()


class _FakeDiscussions:
    def __init__(self):
        self._by_id = {}

    def get(self, discussion_id):
        if discussion_id not in self._by_id:
            self._by_id[discussion_id] = _FakeDiscussion(discussion_id)
        return self._by_id[discussion_id]


class _FakeMergeRequest:
    def __init__(self):
        self.notes = _FakeNotes()
        self.discussions = _FakeDiscussions()


class _FakeMergeRequests:
    def __init__(self, mr):
        self._mr = mr

    def get(self, mr_iid):
        return self._mr


class _FakeProject:
    def __init__(self, mr):
        self.mergerequests = _FakeMergeRequests(mr)


class _FakeProjects:
    def __init__(self, project):
        self._project = project

    def get(self, project_path):
        return self._project


class _FakeGl:
    def __init__(self, project):
        self.projects = _FakeProjects(project)


class _FakeClient:
    def __init__(self, mr):
        self.gl = _FakeGl(_FakeProject(mr))


def test_delete_discussions_dedupes_repeated_note_ids():
    from sensei_ui.engine import delete_discussions

    mr = _FakeMergeRequest()
    client = _FakeClient(mr)

    removed = delete_discussions(
        client, "group/project", 1, ["note-555", "note-555", "note-555", "disc-abc"]
    )

    assert mr.notes.deleted == [555]
    assert removed == 2
