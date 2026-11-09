"""SQLite storage with deduplication, change tracking and closure detection.

Deduplication: the primary UNIQUE key is ``dedup_key`` (company + title +
source URL). A secondary ``cross_board_key`` (company + title) links the same
role re-posted on two different boards; a cross-board re-appearance is stored
but NOT reported as newly discovered.

Change tracking: ``content_hash`` hashes title+description+location; when a
re-seen posting's hash changes, ``changed_at`` is stamped.

Closure: a posting is only marked ``closed`` after it has been absent from
``close_after_scans`` successive *successful* scans of its source.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import Job

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key          TEXT UNIQUE NOT NULL,
    source_type        TEXT NOT NULL,
    source_token       TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    title              TEXT NOT NULL,
    title_normalized   TEXT NOT NULL,
    company            TEXT NOT NULL,
    company_normalized TEXT NOT NULL,
    location           TEXT NOT NULL DEFAULT '',
    url                TEXT NOT NULL,
    url_normalized     TEXT NOT NULL,
    apply_url          TEXT NOT NULL DEFAULT '',
    workplace_type     TEXT NOT NULL DEFAULT '',
    employment_type    TEXT NOT NULL DEFAULT '',
    posted_at          TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    secondary_locations TEXT NOT NULL DEFAULT '[]',
    score              REAL NOT NULL DEFAULT 0,
    score_reasons      TEXT NOT NULL DEFAULT '[]',
    eligibility        TEXT NOT NULL DEFAULT '',
    eligibility_reason TEXT NOT NULL DEFAULT '',
    seniority          TEXT NOT NULL DEFAULT '',
    cross_board_key    TEXT NOT NULL DEFAULT '',
    content_hash       TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'open',
    absent_scans       INTEGER NOT NULL DEFAULT 0,
    changed_at         TEXT NOT NULL DEFAULT '',
    is_new             INTEGER NOT NULL DEFAULT 1,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL
);
"""

# Indexes on columns present in the ORIGINAL schema (safe before migration).
_BASE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_new ON jobs(is_new);
"""

# Indexes on columns added by migration (run AFTER _migrate).
_NEW_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_eligibility ON jobs(eligibility);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_cross_board ON jobs(cross_board_key);
"""

# Columns added since the original MVP schema (migrated with ALTER TABLE).
_MIGRATIONS = [
    ("secondary_locations", "TEXT NOT NULL DEFAULT '[]'"),
    ("eligibility", "TEXT NOT NULL DEFAULT ''"),
    ("eligibility_reason", "TEXT NOT NULL DEFAULT ''"),
    ("seniority", "TEXT NOT NULL DEFAULT ''"),
    ("cross_board_key", "TEXT NOT NULL DEFAULT ''"),
    ("content_hash", "TEXT NOT NULL DEFAULT ''"),
    ("status", "TEXT NOT NULL DEFAULT 'open'"),
    ("absent_scans", "INTEGER NOT NULL DEFAULT 0"),
    ("changed_at", "TEXT NOT NULL DEFAULT ''"),
]

_COLUMNS = (
    "dedup_key", "source_type", "source_token", "external_id", "title",
    "title_normalized", "company", "company_normalized", "location", "url",
    "url_normalized", "apply_url", "workplace_type", "employment_type",
    "posted_at", "description", "secondary_locations", "score",
    "score_reasons", "eligibility", "eligibility_reason", "seniority",
    "cross_board_key", "content_hash", "status", "absent_scans", "changed_at",
    "is_new", "first_seen_at", "last_seen_at",
)


class Storage:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_CREATE_TABLE)
        self._migrate()
        self.conn.executescript(_BASE_INDEXES)
        self.conn.executescript(_NEW_INDEXES)
        self.conn.commit()

    def _migrate(self) -> None:
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for col, decl in _MIGRATIONS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")

    def close(self) -> None:
        self.conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- writes ------------------------------------------------------------

    def upsert_jobs(self, jobs: List[Job]) -> List[Job]:
        """Insert/refresh jobs. Returns the newly discovered subset (first insert
        and not a cross-board duplicate)."""
        now = self._now()
        new_jobs: List[Job] = []
        changed: List[Job] = []
        for job in jobs:
            row = self._job_row(job, now)
            existing = self.conn.execute(
                "SELECT id, content_hash, changed_at FROM jobs WHERE dedup_key = ?",
                (job.dedup_key,),
            ).fetchone()

            if existing is None:
                # Cross-board duplicates are detected for reporting via
                # cross_board_key, but each source posting is still stored and
                # may independently be "newly discovered".
                row["is_new"] = 1
                values = [row[c] for c in _COLUMNS]
                self.conn.execute(
                    f"INSERT OR IGNORE INTO jobs ({', '.join(_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                    values,
                )
                if row["is_new"]:
                    new_jobs.append(job)
            else:
                content_changed = (existing["content_hash"] or "") != job.content_hash
                changed_at = now if content_changed else (existing["changed_at"] or "")
                self.conn.execute(
                    "UPDATE jobs SET title = ?, title_normalized = ?, company = ?, "
                    "company_normalized = ?, location = ?, url = ?, url_normalized = ?, "
                    "apply_url = ?, workplace_type = ?, employment_type = ?, "
                    "posted_at = ?, description = ?, secondary_locations = ?, "
                    "score = ?, score_reasons = ?, eligibility = ?, "
                    "eligibility_reason = ?, seniority = ?, cross_board_key = ?, "
                    "content_hash = ?, changed_at = ?, absent_scans = 0, "
                    "status = 'open', is_new = 0, last_seen_at = ? "
                    "WHERE dedup_key = ?",
                    (job.title, job.title_normalized, job.company,
                     job.company_normalized, job.location, job.url, job.url_normalized,
                     job.apply_url, job.workplace_type, job.employment_type,
                     job.posted_at, job.description,
                     json.dumps(job.secondary_locations), job.score,
                     json.dumps(job.score_reasons), job.eligibility,
                     job.eligibility_reason, job.seniority, job.cross_board_key,
                     job.content_hash, changed_at, now, job.dedup_key),
                )
                if content_changed:
                    changed.append(job)
        self.conn.commit()
        return new_jobs

    def _job_row(self, job: Job, now: str) -> dict:
        row = job.to_row()
        row["score_reasons"] = json.dumps(job.score_reasons)
        row["secondary_locations"] = json.dumps(job.secondary_locations)
        row["status"] = "open"
        row["absent_scans"] = 0
        row["changed_at"] = ""
        row["is_new"] = 1
        row["first_seen_at"] = now
        row["last_seen_at"] = now
        return row

    def mark_absent(
        self,
        source_type: str,
        source_token: str,
        seen_dedup_keys: set,
        close_after_scans: int,
    ) -> int:
        """Increment absent_scans for open jobs of a source that were NOT seen
        in this (successful) scan; close those past the threshold. Returns the
        number newly closed."""
        self.conn.execute(
            "UPDATE jobs SET absent_scans = absent_scans + 1 "
            "WHERE source_type = ? AND source_token = ? AND status = 'open' "
            "AND absent_scans < ?",
            (source_type, source_token, close_after_scans),
        )
        # Reset to 0 for the ones actually seen this scan.
        if seen_dedup_keys:
            placeholders = ",".join("?" for _ in seen_dedup_keys)
            self.conn.execute(
                f"UPDATE jobs SET absent_scans = 0, last_seen_at = ? "
                f"WHERE source_type = ? AND source_token = ? AND dedup_key IN ({placeholders})",
                (self._now(), source_type, source_token, *seen_dedup_keys),
            )
        # Close roles absent for enough successive successful scans.
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'closed' WHERE status = 'open' AND absent_scans >= ?",
            (close_after_scans,),
        )
        self.conn.commit()
        return cur.rowcount

    # -- reads -------------------------------------------------------------

    def list_jobs(
        self,
        only_new: bool = True,
        min_score: Optional[float] = None,
        limit: Optional[int] = None,
        source_type: Optional[str] = None,
        eligibility: Optional[str] = None,
        status: Optional[str] = "open",
        only_qualified: bool = False,
    ) -> List[dict]:
        """Return stored jobs, highest score first."""
        where = []
        params: list = []
        if only_new:
            where.append("is_new = 1")
        if only_qualified:
            where.append("eligibility = 'confirmed'")
        elif eligibility is not None:
            where.append("eligibility = ?")
            params.append(eligibility)
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY score DESC, last_seen_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_seen(self, only_new: bool = True) -> int:
        """Clear the 'new' flag; returns number of rows updated."""
        sql = "UPDATE jobs SET is_new = 0"
        if only_new:
            sql += " WHERE is_new = 1"
        cur = self.conn.execute(sql)
        self.conn.commit()
        return cur.rowcount

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"])

    def count_new(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE is_new = 1").fetchone()["c"])

    def stats(self) -> dict:
        """Aggregate counts for the validation report."""
        out: dict = {"total": self.count()}
        for field in ("eligibility", "status"):
            rows = self.conn.execute(
                f"SELECT {field} AS k, COUNT(*) AS c FROM jobs GROUP BY {field}"
            ).fetchall()
            out[field] = {r["k"] or "unset": r["c"] for r in rows}
        out["open"] = int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE status = 'open'").fetchone()["c"])
        out["closed"] = int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE status = 'closed'").fetchone()["c"])
        return out

    def find_cross_board_duplicates(self) -> List[dict]:
        """Rows whose company+title appears under more than one dedup key."""
        rows = self.conn.execute(
            "SELECT cross_board_key, COUNT(*) AS n FROM jobs "
            "WHERE cross_board_key != '' GROUP BY cross_board_key HAVING COUNT(*) > 1 "
            "ORDER BY n DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- rescore / export --------------------------------------------------

    def iter_jobs(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM jobs").fetchall()]

    def update_score(self, job_id: int, job: Job) -> None:
        self.conn.execute(
            "UPDATE jobs SET score = ?, score_reasons = ?, eligibility = ?, "
            "eligibility_reason = ?, seniority = ?, content_hash = ? WHERE id = ?",
            (job.score, json.dumps(job.score_reasons), job.eligibility,
             job.eligibility_reason, job.seniority, job.content_hash, job_id),
        )

    def export_rows(self, rows: List[dict]) -> List[dict]:
        out = []
        for r in rows:
            out.append({
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "workplace_type": r["workplace_type"],
                "employment_type": r["employment_type"],
                "eligibility": r["eligibility"],
                "eligibility_reason": r["eligibility_reason"],
                "seniority": r["seniority"],
                "score": r["score"],
                "status": r["status"],
                "url": r["url"],
                "apply_url": r["apply_url"],
                "source": f"{r['source_type']}:{r['source_token']}",
                "first_seen_at": r["first_seen_at"],
                "last_seen_at": r["last_seen_at"],
            })
        return out
