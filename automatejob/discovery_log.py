"""Discovery operation accounting.

Every scheduled discovery operation — whether a deterministic API call, RSS
poll, permitted scrape, Gmail alert ingestion, or targeted Firecrawl/Alexandria
query — is recorded with its source, access method, query, timestamps, outcome,
and cost. This gives a trustworthy "33 operations/day" accounting that is not
confused with raw Firecrawl credit spend (an operation may cost zero credits).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_operations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    completed_at     TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL,
    access_method    TEXT NOT NULL DEFAULT '',
    query            TEXT NOT NULL DEFAULT '',
    outcome          TEXT NOT NULL DEFAULT 'pending',  -- pending/success/failure/skipped
    returned_records INTEGER NOT NULL DEFAULT 0,
    new_jobs         INTEGER NOT NULL DEFAULT 0,
    new_companies    INTEGER NOT NULL DEFAULT 0,
    duplicate_count  INTEGER NOT NULL DEFAULT 0,
    firecrawl_credits REAL NOT NULL DEFAULT 0,
    provider_cost     REAL NOT NULL DEFAULT 0,
    error_reason     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_discovery_started ON discovery_operations(started_at);
CREATE INDEX IF NOT EXISTS idx_discovery_source ON discovery_operations(source);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscoveryLog:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def start(self, source: str, *, access_method: str = "", query: str = "",
              provider_cost: float = 0.0) -> int:
        cur = self.conn.execute(
            "INSERT INTO discovery_operations "
            "(started_at, source, access_method, query, provider_cost) VALUES (?,?,?,?,?)",
            (_now(), source, access_method, query, float(provider_cost)))
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def complete(self, operation_id: int, *, outcome: str = "success",
                 returned_records: int = 0, new_jobs: int = 0,
                 new_companies: int = 0, duplicate_count: int = 0,
                 firecrawl_credits: float = 0.0, error_reason: str = "") -> None:
        if outcome not in ("success", "failure", "skipped"):
            raise ValueError(f"invalid discovery outcome {outcome!r}")
        self.conn.execute(
            "UPDATE discovery_operations SET completed_at=?, outcome=?, returned_records=?, "
            "new_jobs=?, new_companies=?, duplicate_count=?, firecrawl_credits=?, "
            "error_reason=? WHERE id=?",
            (_now(), outcome, int(returned_records), int(new_jobs), int(new_companies),
             int(duplicate_count), float(firecrawl_credits), error_reason, operation_id))
        self.conn.commit()

    def list(self, limit: int = 100) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM discovery_operations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def today(self, date_prefix: Optional[str] = None) -> List[dict]:
        prefix = date_prefix or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM discovery_operations WHERE started_at LIKE ? ORDER BY id",
            (prefix + "%",)).fetchall()]

    def summary(self, date_prefix: Optional[str] = None) -> dict:
        prefix = date_prefix or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT outcome, COUNT(*) c, SUM(firecrawl_credits) credits "
            "FROM discovery_operations WHERE started_at LIKE ? GROUP BY outcome",
            (prefix + "%",)).fetchall()
        by_source = self.conn.execute(
            "SELECT source, COUNT(*) c, SUM(firecrawl_credits) credits "
            "FROM discovery_operations WHERE started_at LIKE ? GROUP BY source ORDER BY c DESC",
            (prefix + "%",)).fetchall()
        out = {"total": 0, "by_outcome": {}, "by_source": []}
        for r in rows:
            out["by_outcome"][r["outcome"]] = {"count": int(r["c"]),
                                               "credits": float(r["credits"] or 0)}
            out["total"] += int(r["c"])
        out["by_source"] = [{"source": r["source"], "count": int(r["c"]),
                             "credits": float(r["credits"] or 0)} for r in by_source]
        return out
