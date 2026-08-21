import pytest
from fastapi.testclient import TestClient

from sensei_ui.app import create_app
from sensei_ui.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(str(tmp_path / "ui.db"))
    app = create_app(store=store)
    yield TestClient(app)
    store.close()


def _stub_health(monkeypatch):
    monkeypatch.setattr(
        "sensei_ui.app.setup.health",
        lambda: {
            "gitlab": {"ok": True, "detail": "stub"},
            "claude": {"ok": True, "detail": "stub"},
            "sensei": {"ok": True, "detail": "stub"},
        },
    )


def test_health_reports_each_check(client, monkeypatch):
    """Hermetic: must not shell out to the real `claude` CLI or GitLab."""
    _stub_health(monkeypatch)

    body = client.get("/api/health").json()

    assert "gitlab" in body
    assert "claude" in body
    assert "sensei" in body


def test_unknown_run_returns_404(client):
    assert client.get("/api/runs/999").status_code == 404


def test_get_run_returns_parsed_files(client, monkeypatch):
    """The spec's API contract is run + findings + parsed diff."""
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc")

    class FakeClient:
        def get_mr_diff(self, project_path, mr_iid):
            return {"files": [{"new_path": "a.py",
                               "diff": "@@ -1,1 +1,2 @@\n ctx\n+added\n"}]}

    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: FakeClient())

    body = client.get("/api/runs/%d" % run_id).json()

    assert body["files"][0]["path"] == "a.py"
    added = [r for r in body["files"][0]["rows"] if r["kind"] == "add"]
    assert added[0]["new_line"] == 2


def test_patch_updates_finding_state(client, tmp_path):
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc")
    store.upsert_findings(run_id, [{
        "signature": "inline|a.py|1", "file": "a.py", "line": 1,
        "kind": "must", "confidence": 90, "original_body": "b",
        "verdict": None, "verdict_reason": None,
    }])
    finding_id = store.list_findings(run_id)[0]["id"]

    response = client.patch(
        "/api/findings/%d" % finding_id,
        json={"state": "discarded", "edited_body": "reworded"},
    )

    assert response.status_code == 200
    updated = store.list_findings(run_id)[0]
    assert updated["state"] == "discarded"
    assert updated["edited_body"] == "reworded"


def test_patch_rejects_unknown_state(client):
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc")
    store.upsert_findings(run_id, [{
        "signature": "inline|a.py|1", "file": "a.py", "line": 1,
        "kind": "must", "confidence": 90, "original_body": "b",
        "verdict": None, "verdict_reason": None,
    }])
    finding_id = store.list_findings(run_id)[0]["id"]

    response = client.patch(
        "/api/findings/%d" % finding_id, json={"state": "Kept"}
    )

    assert response.status_code == 422
    assert store.list_findings(run_id)[0]["state"] == "pending"


def test_patch_unknown_finding_returns_404(client):
    response = client.patch("/api/findings/999", json={"state": "kept"})

    assert response.status_code == 404


def test_posting_blocked_when_run_is_stale(client, monkeypatch):
    """Staleness is a hard block — this is the plan's most important rule."""
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "old-sha")

    class FakeClient:
        def get_mr_diff(self, project_path, mr_iid):
            return {
                "files": [{"new_path": "a.py",
                           "diff": "@@ -1,1 +1,2 @@\n ctx\n+added\n"}],
                "base_sha": "b", "head_sha": "new-sha", "start_sha": "s",
            }

        def get_existing_comments(self, project_path, mr_iid):
            return set()

    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: FakeClient())

    post_planned_calls = []
    monkeypatch.setattr(
        "sensei_ui.app.engine.post_planned",
        lambda *args, **kwargs: post_planned_calls.append((args, kwargs)),
    )

    response = client.post("/api/runs/%d/post" % run_id)

    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()
    assert post_planned_calls == []


def test_posting_proceeds_when_head_matches(client, monkeypatch):
    """The positive counterpart to the staleness block: a matching head must
    not be rejected, and must post using the SAME mr_data that was checked
    (no second, possibly-newer fetch)."""
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "same-sha")
    store.upsert_findings(run_id, [{
        "signature": "inline|a.py|1", "file": "a.py", "line": 1,
        "kind": "must", "confidence": 90, "original_body": "b",
        "verdict": None, "verdict_reason": None,
    }])
    finding_id = store.list_findings(run_id)[0]["id"]
    store.update_finding(finding_id, state="kept")

    class FakeClient:
        def __init__(self):
            self.get_mr_diff_calls = 0

        def get_mr_diff(self, project_path, mr_iid):
            self.get_mr_diff_calls += 1
            return {
                "files": [{"new_path": "a.py",
                           "diff": "@@ -1,1 +1,2 @@\n ctx\n+added\n"}],
                "base_sha": "b", "head_sha": "same-sha", "start_sha": "s",
            }

        def get_existing_comments(self, project_path, mr_iid):
            return set()

    fake_client = FakeClient()
    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: fake_client)

    captured = {}

    def fake_post_planned(client_arg, project_path, mr_iid, diff_refs, planned, on_posted):
        captured["diff_refs"] = diff_refs
        captured["planned"] = planned
        return {"posted": len(planned["inline"]) + len(planned["summary"]),
                "skipped": len(planned["skipped"])}

    monkeypatch.setattr("sensei_ui.app.engine.post_planned", fake_post_planned)

    response = client.post("/api/runs/%d/post" % run_id)

    assert response.status_code == 200
    assert response.json() == {"posted": 1, "skipped": 0}
    assert captured["diff_refs"] == {
        "base_sha": "b", "head_sha": "same-sha", "start_sha": "s",
    }
    assert fake_client.get_mr_diff_calls == 1


def _stub_generate(monkeypatch, findings, test_summary=None, head_sha="sha-1"):
    monkeypatch.setattr(
        "sensei_ui.app.engine.generate",
        lambda mr_url: {
            "project_path": "g/p",
            "mr_iid": 7,
            "head_sha": head_sha,
            "findings": findings,
            "test_summary": test_summary,
            "files": [],
            "diff_refs": {},
        },
    )
    monkeypatch.setattr(
        "sensei_ui.app.verify.adjudicate", lambda findings, diff: (findings, "ok")
    )


def _generated(signature, body="generated body"):
    return {
        "signature": signature, "file": "a.py", "line": 1, "kind": "must",
        "confidence": 90, "original_body": body,
        "verdict": None, "verdict_reason": None,
    }


def test_rerun_of_the_same_mr_preserves_edits_and_discards(client, monkeypatch):
    """The whole point of the store: re-reviewing must not wipe triage."""
    _stub_generate(monkeypatch, [_generated("sig-1"), _generated("sig-2")])
    store = client.app.state.store

    first_run = client.post("/api/runs", json={"mr_url": "https://x/7"}).json()
    by_sig = {f["signature"]: f for f in store.list_findings(first_run["run_id"])}
    client.patch(
        "/api/findings/%d" % by_sig["sig-1"]["id"],
        json={"state": "kept", "edited_body": "my wording"},
    )
    client.patch(
        "/api/findings/%d" % by_sig["sig-2"]["id"], json={"state": "discarded"}
    )

    _stub_generate(
        monkeypatch,
        [_generated("sig-1", "regenerated"), _generated("sig-2", "regenerated")],
        head_sha="sha-2",
    )
    second_run = client.post("/api/runs", json={"mr_url": "https://x/7"}).json()

    assert second_run["run_id"] == first_run["run_id"]
    after = {f["signature"]: f for f in store.list_findings(second_run["run_id"])}
    assert after["sig-1"]["state"] == "kept"
    assert after["sig-1"]["edited_body"] == "my wording"
    assert after["sig-2"]["state"] == "discarded"
    assert store.get_run(second_run["run_id"])["head_sha"] == "sha-2"


def test_run_persists_and_returns_the_test_summary(client, monkeypatch):
    """Test-coverage findings must reach the reviewer, not be dropped."""
    summary = {"uncovered": ["a.py::parse"], "note": "add a regression test"}
    _stub_generate(monkeypatch, [_generated("sig-1")], test_summary=summary)

    run_id = client.post("/api/runs", json={"mr_url": "https://x/7"}).json()["run_id"]

    class FakeClient:
        def get_mr_diff(self, project_path, mr_iid):
            return {"files": []}

    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: FakeClient())

    body = client.get("/api/runs/%d" % run_id).json()

    assert body["test_summary"] == summary
    assert body["run"]["test_summary"] == summary


def test_patch_rejects_client_supplied_posted_state(client):
    """Only mark_posted may set `posted`; a fabricated one breaks undo."""
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "abc")
    store.upsert_findings(run_id, [{
        "signature": "inline|a.py|1", "file": "a.py", "line": 1,
        "kind": "must", "confidence": 90, "original_body": "b",
        "verdict": None, "verdict_reason": None,
    }])
    finding_id = store.list_findings(run_id)[0]["id"]

    response = client.patch(
        "/api/findings/%d" % finding_id, json={"state": "posted"}
    )

    assert response.status_code == 422
    after = store.list_findings(run_id)[0]
    assert after["state"] == "pending"
    assert after["discussion_id"] is None


def _seed_postable_run(client, states):
    store = client.app.state.store
    run_id = store.get_or_create_run("g/p", 7, "https://x/7", "same-sha")
    store.upsert_findings(run_id, [
        {
            "signature": "inline|a.py|1#%s" % state, "file": "a.py", "line": 1,
            "kind": "must", "confidence": 90, "original_body": state,
            "verdict": None, "verdict_reason": None,
        }
        for state in states
    ])
    for finding in store.list_findings(run_id):
        store.update_finding(finding["id"], state=finding["original_body"])
    return run_id


class _MatchingHeadClient:
    def get_mr_diff(self, project_path, mr_iid):
        return {
            "files": [{"new_path": "a.py",
                       "diff": "@@ -1,1 +1,2 @@\n ctx\n+added\n"}],
            "base_sha": "b", "head_sha": "same-sha", "start_sha": "s",
        }

    def get_existing_comments(self, project_path, mr_iid):
        return set()


def test_only_kept_findings_are_planned_for_posting(client, monkeypatch):
    """The kept filter is the last gate before a colleague's merge request."""
    run_id = _seed_postable_run(client, ["pending", "discarded", "kept"])
    monkeypatch.setattr(
        "sensei_ui.app.engine.make_client", lambda url: _MatchingHeadClient()
    )

    captured = {}

    def fake_post_planned(client_arg, project_path, mr_iid, diff_refs, planned, on_posted):
        captured["planned"] = planned
        return {"posted": 0, "skipped": 0}

    monkeypatch.setattr("sensei_ui.app.engine.post_planned", fake_post_planned)

    assert client.post("/api/runs/%d/post" % run_id).status_code == 200

    planned = captured["planned"]
    bodies = [e["original_body"]
              for e in planned["inline"] + planned["summary"] + planned["skipped"]]
    assert bodies == ["kept"]


def test_concurrent_posts_produce_exactly_one_set_of_posts(client, monkeypatch):
    """A double-clicked Post must not double-post to someone else's MR."""
    import threading
    import time

    run_id = _seed_postable_run(client, ["kept"])
    monkeypatch.setattr(
        "sensei_ui.app.engine.make_client", lambda url: _MatchingHeadClient()
    )

    entered = threading.Event()
    planned_counts = []

    def fake_post_planned(client_arg, project_path, mr_iid, diff_refs, planned, on_posted):
        entries = planned["inline"] + planned["summary"]
        planned_counts.append(len(entries))
        entered.set()
        time.sleep(0.2)
        for entry in entries:
            on_posted(entry, "disc-%d" % entry["id"])
        return {"posted": len(entries), "skipped": len(planned["skipped"])}

    monkeypatch.setattr("sensei_ui.app.engine.post_planned", fake_post_planned)

    results = []

    def fire():
        results.append(client.post("/api/runs/%d/post" % run_id).json())

    first = threading.Thread(target=fire)
    first.start()
    entered.wait(timeout=5)
    second = threading.Thread(target=fire)
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert sum(r["posted"] for r in results) == 1
    assert sorted(planned_counts) == [0, 1]


def test_kept_finding_absent_from_latest_generation_is_not_posted(
    client, monkeypatch
):
    """The 2026-08-21 failure in miniature: never comment on fixed code."""
    _stub_generate(monkeypatch, [_generated("sig-stale"), _generated("sig-live")])
    store = client.app.state.store

    run_id = client.post("/api/runs", json={"mr_url": "https://x/7"}).json()[
        "run_id"
    ]
    for finding in store.list_findings(run_id):
        client.patch("/api/findings/%d" % finding["id"], json={"state": "kept"})

    _stub_generate(monkeypatch, [_generated("sig-live")], head_sha="same-sha")
    client.post("/api/runs", json={"mr_url": "https://x/7"})

    monkeypatch.setattr(
        "sensei_ui.app.engine.make_client", lambda url: _MatchingHeadClient()
    )
    captured = {}

    def fake_post_planned(
        client_arg, project_path, mr_iid, diff_refs, planned, on_posted
    ):
        captured["planned"] = planned
        return {"posted": 0, "skipped": 0}

    monkeypatch.setattr("sensei_ui.app.engine.post_planned", fake_post_planned)

    assert client.post("/api/runs/%d/post" % run_id).status_code == 200

    planned = captured["planned"]
    signatures = [
        e["signature"]
        for e in planned["inline"] + planned["summary"] + planned["skipped"]
    ]
    assert signatures == ["sig-live"]
