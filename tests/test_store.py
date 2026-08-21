import pytest

from sensei_ui.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "ui.db"))
    yield s
    s.close()


def _finding(signature, body="original text"):
    return {
        "signature": signature,
        "file": "a.py",
        "line": 10,
        "kind": "must",
        "confidence": 95,
        "original_body": body,
        "verdict": None,
        "verdict_reason": None,
    }


def test_get_or_create_run_returns_id_and_round_trips(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc123")
    run = store.get_run(run_id)

    assert run["mr_iid"] == 7
    assert run["head_sha"] == "abc123"
    assert run["status"] == "running"
    assert run["verify_status"] == "skipped"


def test_upsert_inserts_findings_as_pending(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])

    findings = store.list_findings(run_id)
    assert len(findings) == 1
    assert findings[0]["state"] == "pending"
    assert findings[0]["edited_body"] is None


def test_reupsert_preserves_edited_body_and_state(store):
    """The core requirement: a regenerating re-run must not erase decisions."""
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])

    finding_id = store.list_findings(run_id)[0]["id"]
    store.update_finding(finding_id, edited_body="my wording", state="discarded")

    store.upsert_findings(run_id, [_finding("sig-1", body="regenerated text")])

    after = store.list_findings(run_id)
    assert len(after) == 1
    assert after[0]["edited_body"] == "my wording"
    assert after[0]["state"] == "discarded"
    assert after[0]["original_body"] == "regenerated text"


def test_reupsert_adds_new_findings_without_touching_old(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])
    store.update_finding(store.list_findings(run_id)[0]["id"], None, "kept")

    store.upsert_findings(run_id, [_finding("sig-1"), _finding("sig-2")])

    by_sig = {f["signature"]: f for f in store.list_findings(run_id)}
    assert by_sig["sig-1"]["state"] == "kept"
    assert by_sig["sig-2"]["state"] == "pending"


def test_mark_posted_records_discussion_id(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])
    finding_id = store.list_findings(run_id)[0]["id"]

    store.mark_posted(finding_id, "disc-abc")

    posted = store.list_findings(run_id)[0]
    assert posted["state"] == "posted"
    assert posted["discussion_id"] == "disc-abc"
    assert posted["posted_at"] is not None


def test_get_or_create_run_reuses_the_row_for_one_merge_request(store):
    """A merge request owns one run row, so decisions keyed on it survive."""
    first = store.get_or_create_run("g/p", 7, "https://x/7", "sha-one")
    second = store.get_or_create_run("g/p", 7, "https://x/7", "sha-two")

    assert first == second
    run = store.get_run(first)
    assert run["head_sha"] == "sha-two"
    assert run["status"] == "running"
    assert run["verify_status"] == "skipped"


def test_get_or_create_run_separates_different_merge_requests(store):
    first = store.get_or_create_run("g/p", 7, "https://x/7", "sha")
    other_mr = store.get_or_create_run("g/p", 8, "https://x/8", "sha")
    other_project = store.get_or_create_run("g/q", 7, "https://y/7", "sha")

    assert len({first, other_mr, other_project}) == 3


def test_reused_run_keeps_posted_findings_posted(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "sha-one")
    store.upsert_findings(run_id, [_finding("sig-1")])
    store.mark_posted(store.list_findings(run_id)[0]["id"], "disc-1")

    store.get_or_create_run("g/p", 7, "https://x/7", "sha-two")
    store.upsert_findings(run_id, [_finding("sig-1", body="regenerated")])

    after = store.list_findings(run_id)[0]
    assert after["state"] == "posted"
    assert after["discussion_id"] == "disc-1"


def test_set_run_test_summary_round_trips(store):
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "sha")

    assert store.get_run(run_id)["test_summary"] is None

    store.set_run_test_summary(run_id, '{"covered": 3}')

    assert store.get_run(run_id)["test_summary"] == '{"covered": 3}'


def test_finding_missing_from_the_next_generation_drops_out(store):
    """A kept finding the author has since fixed must not linger as postable."""
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "sha-1")
    store.upsert_findings(run_id, [_finding("sig-stale"), _finding("sig-live")])
    for finding in store.list_findings(run_id):
        store.update_finding(finding["id"], None, "kept")

    same_run = store.get_or_create_run("g/p", 7, "https://x/7", "sha-2")
    store.upsert_findings(same_run, [_finding("sig-live")])

    remaining = store.list_findings(run_id)
    assert [f["signature"] for f in remaining] == ["sig-live"]
    assert remaining[0]["state"] == "kept"
