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


def test_health_reports_each_check(client):
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


def test_posting_blocked_when_run_is_stale(client, monkeypatch):
    """Staleness is a hard block — this is the plan's most important rule."""
    store = client.app.state.store
    run_id = store.create_run("g/p", 7, "https://x/7", "old-sha")

    monkeypatch.setattr(
        "sensei_ui.app.current_head_sha", lambda mr_url: "new-sha"
    )

    response = client.post("/api/runs/%d/post" % run_id)

    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()
