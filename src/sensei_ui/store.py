"""SQLite persistence for reviewer decisions.

Sensei's snapshots record what was *generated*; this records what the reviewer
*decided*. They must stay separate because `sensei review` regenerates findings
on every run and would overwrite any decision stored alongside them.
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    id            INTEGER PRIMARY KEY,
    project_path  TEXT NOT NULL,
    mr_iid        INTEGER NOT NULL,
    mr_url        TEXT NOT NULL,
    head_sha      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL,
    verify_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding (
    id             INTEGER PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES run(id),
    signature      TEXT NOT NULL,
    file           TEXT NOT NULL,
    line           INTEGER,
    kind           TEXT NOT NULL,
    confidence     INTEGER,
    original_body  TEXT NOT NULL,
    edited_body    TEXT,
    state          TEXT NOT NULL,
    verdict        TEXT,
    verdict_reason TEXT,
    discussion_id  TEXT,
    posted_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS finding_run_signature
    ON finding (run_id, signature);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str):
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_run(
        self, project_path: str, mr_iid: int, mr_url: str, head_sha: str
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO run (project_path, mr_iid, mr_url, head_sha,"
                " created_at, status, verify_status)"
                " VALUES (?, ?, ?, ?, ?, 'running', 'skipped')",
                (project_path, mr_iid, mr_url, head_sha, _now()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def set_run_status(
        self, run_id: int, status: str, verify_status: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE run SET status = ?, verify_status = ? WHERE id = ?",
                (status, verify_status, run_id),
            )
            self.conn.commit()

    def get_run(self, run_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM run WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_findings(self, run_id: int, findings: List[Dict]) -> None:
        """Insert new findings; refresh generated text on existing ones.

        Reviewer-owned columns (edited_body, state, discussion_id, posted_at)
        are deliberately left untouched so decisions survive regeneration.
        """
        with self._lock:
            for f in findings:
                self.conn.execute(
                    "INSERT INTO finding (run_id, signature, file, line, kind,"
                    " confidence, original_body, state, verdict, verdict_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)"
                    " ON CONFLICT(run_id, signature) DO UPDATE SET"
                    "   original_body = excluded.original_body,"
                    "   confidence    = excluded.confidence,"
                    "   verdict       = excluded.verdict,"
                    "   verdict_reason = excluded.verdict_reason",
                    (
                        run_id,
                        f["signature"],
                        f["file"],
                        f.get("line"),
                        f["kind"],
                        f.get("confidence"),
                        f["original_body"],
                        f.get("verdict"),
                        f.get("verdict_reason"),
                    ),
                )
            self.conn.commit()

    def list_findings(self, run_id: int) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM finding WHERE run_id = ? ORDER BY file, line, id",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_finding(self, finding_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM finding WHERE id = ?", (finding_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_finding(
        self,
        finding_id: int,
        edited_body: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        with self._lock:
            if edited_body is not None:
                self.conn.execute(
                    "UPDATE finding SET edited_body = ? WHERE id = ?",
                    (edited_body, finding_id),
                )
            if state is not None:
                self.conn.execute(
                    "UPDATE finding SET state = ? WHERE id = ?",
                    (state, finding_id),
                )
            self.conn.commit()

    def mark_posted(self, finding_id: int, discussion_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE finding SET state = 'posted', discussion_id = ?,"
                " posted_at = ? WHERE id = ?",
                (discussion_id, _now(), finding_id),
            )
            self.conn.commit()
