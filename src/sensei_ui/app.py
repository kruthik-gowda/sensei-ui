"""FastAPI application wiring the modules together."""
import json
import os
import threading
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sensei_ui import diff as diffmod
from sensei_ui import engine, setup, verify
from sensei_ui.store import Store

DB_PATH = os.path.expanduser("~/.sensei-ui/ui.db")
ALLOWED_FINDING_STATES = frozenset({"pending", "kept", "discarded"})

_POST_LOCKS: Dict[int, threading.Lock] = {}
_POST_LOCKS_GUARD = threading.Lock()


def _post_lock(run_id: int) -> threading.Lock:
    """One lock per run, held across the whole read-plan-post-mark sequence.

    Sync endpoints run on uvicorn's threadpool, so a double-clicked Post can
    have two requests read the same kept list before either marks anything
    posted. The store's per-method lock cannot span that sequence.
    """
    with _POST_LOCKS_GUARD:
        lock = _POST_LOCKS.get(run_id)
        if lock is None:
            lock = threading.Lock()
            _POST_LOCKS[run_id] = lock
        return lock


def _decode_test_summary(raw: Optional[str]):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class FindingPatch(BaseModel):
    state: Optional[str] = None
    edited_body: Optional[str] = None


class RunRequest(BaseModel):
    mr_url: str


def create_app(store: Optional[Store] = None) -> FastAPI:
    app = FastAPI(title="sensei-ui")
    app.state.store = store or Store(DB_PATH)

    @app.get("/api/health")
    def get_health() -> Dict:
        return setup.health()

    @app.post("/api/runs")
    def start_run(request: RunRequest) -> Dict:
        try:
            generated = engine.generate(request.mr_url)
        except engine.EngineError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        run_id = app.state.store.get_or_create_run(
            generated["project_path"],
            generated["mr_iid"],
            request.mr_url,
            generated["head_sha"],
        )

        diff_text = "\n".join(
            f.get("diff", "") for f in generated["files"] if f.get("diff")
        )
        try:
            annotated, verify_status = verify.adjudicate(
                generated["findings"], diff_text
            )
        except Exception:  # noqa: BLE001 - a failed run must be recorded, not lost
            app.state.store.set_run_status(run_id, "failed", "failed")
            raise

        app.state.store.upsert_findings(run_id, annotated)
        test_summary = generated.get("test_summary")
        app.state.store.set_run_test_summary(
            run_id,
            None if test_summary is None else json.dumps(test_summary),
        )
        app.state.store.set_run_status(run_id, "complete", verify_status)
        return {"run_id": run_id, "verify_status": verify_status}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int) -> Dict:
        run = app.state.store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")

        client = engine.make_client(run["mr_url"])
        mr_data = client.get_mr_diff(run["project_path"], run["mr_iid"])
        files = [
            {
                "path": f["new_path"],
                "rows": diffmod.parse_diff(f.get("diff") or ""),
            }
            for f in mr_data["files"]
        ]
        test_summary = _decode_test_summary(run.get("test_summary"))
        run = dict(run, test_summary=test_summary)
        return {
            "run": run,
            "findings": app.state.store.list_findings(run_id),
            "files": files,
            "test_summary": test_summary,
        }

    @app.patch("/api/findings/{finding_id}")
    def patch_finding(finding_id: int, patch: FindingPatch) -> Dict:
        if patch.state is not None and patch.state not in ALLOWED_FINDING_STATES:
            raise HTTPException(
                status_code=422,
                detail="invalid state %r, must be one of %s"
                % (patch.state, sorted(ALLOWED_FINDING_STATES)),
            )
        if app.state.store.get_finding(finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")

        app.state.store.update_finding(
            finding_id, patch.edited_body, patch.state
        )
        return {"ok": True}

    @app.post("/api/runs/{run_id}/post")
    def post_run(run_id: int) -> Dict:
        with _post_lock(run_id):
            return _post_run_locked(run_id)

    @app.post("/api/runs/{run_id}/unpost")
    def unpost_run(run_id: int) -> Dict:
        """Delete the threads this run created and return them to `kept`.

        Shares the post lock so an undo cannot interleave with a post.
        """
        run = app.state.store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")

        with _post_lock(run_id):
            posted = app.state.store.list_posted_findings(run_id)
            if not posted:
                return {"removed": 0, "restored": 0}

            client = engine.make_client(run["mr_url"])
            removed = engine.delete_discussions(
                client,
                run["project_path"],
                run["mr_iid"],
                [f["discussion_id"] for f in posted],
            )
            for finding in posted:
                app.state.store.mark_unposted(finding["id"])

            return {"removed": removed, "restored": len(posted)}

    def _post_run_locked(run_id: int) -> Dict:
        run = app.state.store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")

        client = engine.make_client(run["mr_url"])
        mr_data = client.get_mr_diff(run["project_path"], run["mr_iid"])

        if mr_data["head_sha"] != run["head_sha"]:
            raise HTTPException(
                status_code=409,
                detail="Review is stale: the MR has new commits since"
                " %s. Re-review before posting." % run["head_sha"][:8],
            )

        diff_lines_map = {
            f["new_path"]: engine.extract_diff_lines(f["diff"])
            for f in mr_data["files"]
            if f.get("diff")
        }
        existing = client.get_existing_comments(
            run["project_path"], run["mr_iid"]
        )
        kept = [
            f for f in app.state.store.list_findings(run_id)
            if f["state"] == "kept"
        ]
        planned = engine.plan_posts(kept, diff_lines_map, existing)

        def on_posted(entry, discussion_id):
            app.state.store.mark_posted(entry["id"], discussion_id)

        diff_refs = {
            "base_sha": mr_data["base_sha"],
            "head_sha": mr_data["head_sha"],
            "start_sha": mr_data["start_sha"],
        }
        return engine.post_planned(
            client,
            run["project_path"],
            run["mr_iid"],
            diff_refs,
            planned,
            on_posted,
        )

    return app
