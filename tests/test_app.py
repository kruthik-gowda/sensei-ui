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
    run_id = store.create_run("g/p", 7, "https://x/7", "abc")

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
    run_id = store.create_run("g/p", 7, "https://x/7", "abc")
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
    run_id = store.create_run("g/p", 7, "https://x/7", "abc")
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
    run_id = store.create_run("g/p", 7, "https://x/7", "old-sha")

    class FakeClient:
        def get_mr_diff(self, project_path, mr_iid):
            return {"files": [], "base_sha": "b", "head_sha": "new-sha",
                     "start_sha": "s"}

    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: FakeClient())

    response = client.post("/api/runs/%d/post" % run_id)

    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()


def test_posting_proceeds_when_head_matches(client, monkeypatch):
    """The positive counterpart to the staleness block: a matching head must
    not be rejected, and must post using the SAME mr_data that was checked
    (no second, possibly-newer fetch)."""
    store = client.app.state.store
    run_id = store.create_run("g/p", 7, "https://x/7", "same-sha")
    store.upsert_findings(run_id, [{
        "signature": "inline|a.py|1", "file": "a.py", "line": 1,
        "kind": "must", "confidence": 90, "original_body": "b",
        "verdict": None, "verdict_reason": None,
    }])
    finding_id = store.list_findings(run_id)[0]["id"]
    store.update_finding(finding_id, state="kept")

    class FakeClient:
        def get_mr_diff(self, project_path, mr_iid):
            return {
                "files": [{"new_path": "a.py",
                           "diff": "@@ -1,1 +1,2 @@\n ctx\n+added\n"}],
                "base_sha": "b", "head_sha": "same-sha", "start_sha": "s",
            }

        def get_existing_comments(self, project_path, mr_iid):
            return set()

    monkeypatch.setattr("sensei_ui.app.engine.make_client", lambda url: FakeClient())

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
