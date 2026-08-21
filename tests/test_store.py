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


def test_create_run_returns_id_and_round_trips(store):
    run_id = store.create_run("g/p", 7, "https://x/7", "abc123")
    run = store.get_run(run_id)

    assert run["mr_iid"] == 7
    assert run["head_sha"] == "abc123"
    assert run["status"] == "running"
    assert run["verify_status"] == "skipped"


def test_upsert_inserts_findings_as_pending(store):
    run_id = store.create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])

    findings = store.list_findings(run_id)
    assert len(findings) == 1
    assert findings[0]["state"] == "pending"
    assert findings[0]["edited_body"] is None


def test_reupsert_preserves_edited_body_and_state(store):
    """The core requirement: a regenerating re-run must not erase decisions."""
    run_id = store.create_run("g/p", 7, "https://x/7", "abc123")
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
    run_id = store.create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])
    store.update_finding(store.list_findings(run_id)[0]["id"], None, "kept")

    store.upsert_findings(run_id, [_finding("sig-1"), _finding("sig-2")])

    by_sig = {f["signature"]: f for f in store.list_findings(run_id)}
    assert by_sig["sig-1"]["state"] == "kept"
    assert by_sig["sig-2"]["state"] == "pending"


def test_mark_posted_records_discussion_id(store):
    run_id = store.create_run("g/p", 7, "https://x/7", "abc123")
    store.upsert_findings(run_id, [_finding("sig-1")])
    finding_id = store.list_findings(run_id)[0]["id"]

    store.mark_posted(finding_id, "disc-abc")

    posted = store.list_findings(run_id)[0]
    assert posted["state"] == "posted"
    assert posted["discussion_id"] == "disc-abc"
    assert posted["posted_at"] is not None
