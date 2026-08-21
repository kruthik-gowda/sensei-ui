"""FastAPI application wiring the modules together."""
import os
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sensei_ui import diff as diffmod
from sensei_ui import engine, setup, verify
from sensei_ui.store import Store

DB_PATH = os.path.expanduser("~/.sensei-ui/ui.db")


class FindingPatch(BaseModel):
    state: Optional[str] = None
    edited_body: Optional[str] = None


class RunRequest(BaseModel):
    mr_url: str


def current_head_sha(mr_url: str) -> str:
    """Live head SHA, so staleness can be detected at post time."""
    client = engine.make_client(mr_url)
    target = engine.parse_review_url(mr_url)
    mr_data = client.get_mr_diff(
        str(target["project_path"]), int(target["review_id"])
    )
    # get_mr_diff returns base_sha/head_sha/start_sha flat, not nested.
    return mr_data["head_sha"]


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

        run_id = app.state.store.create_run(
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
        return {
            "run": run,
            "findings": app.state.store.list_findings(run_id),
            "files": files,
        }

    @app.patch("/api/findings/{finding_id}")
    def patch_finding(finding_id: int, patch: FindingPatch) -> Dict:
        app.state.store.update_finding(
            finding_id, patch.edited_body, patch.state
        )
        return {"ok": True}

    @app.post("/api/runs/{run_id}/post")
    def post_run(run_id: int) -> Dict:
        run = app.state.store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")

        if current_head_sha(run["mr_url"]) != run["head_sha"]:
            raise HTTPException(
                status_code=409,
                detail="Review is stale: the MR has new commits since"
                " %s. Re-review before posting." % run["head_sha"][:8],
            )

        client = engine.make_client(run["mr_url"])
        mr_data = client.get_mr_diff(run["project_path"], run["mr_iid"])
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
