"""Source Registry: one consistent record per discovery source.

Statuses are explicit — an unsupported/unauthorized source is never reported as
active. The registry records access method, auth requirement, permission,
credential reference (never the credential), refresh policy, rate limit, credit
budget, last successful collection, last verified record count and error count.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class SourceStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    UNSUPPORTED = "unsupported"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_registry (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT UNIQUE NOT NULL,
    source_type          TEXT NOT NULL DEFAULT '',
    access_method        TEXT NOT NULL DEFAULT '',
    classification       TEXT NOT NULL DEFAULT '',  -- api/rss/browser/manual
    auth_required        INTEGER NOT NULL DEFAULT 0,
    permission           TEXT NOT NULL DEFAULT 'unknown',  -- authorized/unauthorized/unknown
    credential_reference  TEXT NOT NULL DEFAULT '',  -- env var / file path only
    refresh_policy       TEXT NOT NULL DEFAULT '',
    rate_limit           TEXT NOT NULL DEFAULT '',
    credit_budget        TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'unsupported',
    last_successful_at   TEXT NOT NULL DEFAULT '',
    last_verified_records INTEGER NOT NULL DEFAULT 0,
    error_count          INTEGER NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceRegistry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def upsert(self, name: str, source_type: str, *, access: str = "",
               classification: str = "", auth_required: bool = False,
               permission: str = "unknown", credential_reference: str = "",
               refresh_policy: str = "", rate_limit: str = "",
               credit_budget: str = "", status: str = SourceStatus.UNSUPPORTED) -> int:
        if status not in (SourceStatus.ACTIVE, SourceStatus.PAUSED, SourceStatus.UNSUPPORTED):
            raise ValueError(f"invalid source status {status!r}")
        existing = self.conn.execute(
            "SELECT id FROM source_registry WHERE name=?", (name,)).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE source_registry SET source_type=?, access_method=?, classification=?, "
                "auth_required=?, permission=?, credential_reference=?, refresh_policy=?, "
                "rate_limit=?, credit_budget=?, status=?, updated_at=? WHERE name=?",
                (source_type, access, classification, int(auth_required), permission,
                 credential_reference, refresh_policy, rate_limit, credit_budget,
                 status, _now(), name))
            sid = existing["id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO source_registry (name,source_type,access_method,classification,"
                "auth_required,permission,credential_reference,refresh_policy,rate_limit,"
                "credit_budget,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, source_type, access, classification, int(auth_required), permission,
                 credential_reference, refresh_policy, rate_limit, credit_budget,
                 status, _now()))
            sid = int(cur.lastrowid or 0)
        self.conn.commit()
        return sid

    def get(self, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM source_registry WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def list_all(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM source_registry ORDER BY name").fetchall()]

    def list_active(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM source_registry WHERE status='active' ORDER BY name").fetchall()]

    def record_success(self, source_id: int, verified_records: int = 0) -> None:
        self.conn.execute(
            "UPDATE source_registry SET last_successful_at=?, "
            "last_verified_records=?, updated_at=? WHERE id=?",
            (_now(), int(verified_records), _now(), source_id))
        self.conn.commit()

    def record_error(self, source_id: int, note: str = "") -> None:
        self.conn.execute(
            "UPDATE source_registry SET error_count=error_count+1, updated_at=? WHERE id=?",
            (_now(), source_id))
        self.conn.commit()
