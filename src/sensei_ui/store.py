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
    verify_status TEXT NOT NULL,
    test_summary  TEXT,
    generation    INTEGER NOT NULL DEFAULT 0
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
    posted_at      TEXT,
    last_seen_generation INTEGER NOT NULL DEFAULT 0
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
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        run_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(run)").fetchall()
        }
        if "test_summary" not in run_columns:
            self.conn.execute("ALTER TABLE run ADD COLUMN test_summary TEXT")
        if "generation" not in run_columns:
            self.conn.execute(
                "ALTER TABLE run ADD COLUMN generation INTEGER NOT NULL"
                " DEFAULT 0"
            )

        finding_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(finding)").fetchall()
        }
        if "last_seen_generation" not in finding_columns:
            self.conn.execute(
                "ALTER TABLE finding ADD COLUMN last_seen_generation"
                " INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self.conn.close()

    def get_or_create_run(
        self, project_path: str, mr_iid: int, mr_url: str, head_sha: str
    ) -> int:
        """Return the run for this merge request, creating it only once.

        A merge request owns exactly one run row. Re-reviewing repoints that
        row at the new head SHA and resets its progress fields, which is what
        keeps the finding table's (run_id, signature) merge — and therefore
        every reviewer decision — alive across regeneration.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM run WHERE project_path = ? AND mr_iid = ?"
                " ORDER BY id DESC LIMIT 1",
                (project_path, mr_iid),
            ).fetchone()
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO run (project_path, mr_iid, mr_url, head_sha,"
                    " created_at, status, verify_status, generation)"
                    " VALUES (?, ?, ?, ?, ?, 'running', 'skipped', 1)",
                    (project_path, mr_iid, mr_url, head_sha, _now()),
                )
                self.conn.commit()
                return int(cur.lastrowid)

            run_id = int(row["id"])
            self.conn.execute(
                "UPDATE run SET mr_url = ?, head_sha = ?, status = 'running',"
                " verify_status = 'skipped', generation = generation + 1"
                " WHERE id = ?",
                (mr_url, head_sha, run_id),
            )
            self.conn.commit()
            return run_id

    def set_run_test_summary(
        self, run_id: int, test_summary: Optional[str]
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE run SET test_summary = ? WHERE id = ?",
                (test_summary, run_id),
            )
            self.conn.commit()

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

        Each finding is stamped with the run's current generation. A finding
        the latest generation did not produce keeps its old stamp and drops out
        of `list_findings` — otherwise a kept finding whose problem the author
        has since fixed would still be posted, which is the exact failure this
        tool exists to prevent.
        """
        with self._lock:
            generation = self._current_generation(run_id)
            for f in findings:
                self.conn.execute(
                    "INSERT INTO finding (run_id, signature, file, line, kind,"
                    " confidence, original_body, state, verdict, verdict_reason,"
                    " last_seen_generation)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)"
                    " ON CONFLICT(run_id, signature) DO UPDATE SET"
                    "   original_body = excluded.original_body,"
                    "   confidence    = excluded.confidence,"
                    "   verdict       = excluded.verdict,"
                    "   verdict_reason = excluded.verdict_reason,"
                    "   last_seen_generation = excluded.last_seen_generation",
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
                        generation,
                    ),
                )
            self.conn.commit()

    def _current_generation(self, run_id: int) -> int:
        """Caller must hold self._lock."""
        row = self.conn.execute(
            "SELECT generation FROM run WHERE id = ?", (run_id,)
        ).fetchone()
        return int(row["generation"]) if row else 0

    def list_findings(self, run_id: int) -> List[Dict]:
        """Findings the latest generation produced. Stale ones are excluded."""
        with self._lock:
            generation = self._current_generation(run_id)
            rows = self.conn.execute(
                "SELECT * FROM finding WHERE run_id = ?"
                " AND last_seen_generation = ?"
                " ORDER BY file, line, id",
                (run_id, generation),
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
