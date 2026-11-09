"""Company intelligence model, storage, and the CompanySource interface.

This is independent of the job-source layer (sources/greenhouse.py etc.).
A company record is a researched, provenance-tracked fact set — unknown fields
are stored as ``None``/``"unknown"``, never guessed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

# Opportunity categories (company-level).
CAT_ACTIVE_HIRING = "active_hiring"
CAT_SPECULATIVE = "speculative_outreach"
CAT_RESEARCH_LEAD = "research_lead"

# Remote hiring policy values.
REMOTE_INDIA = "india_remote"
REMOTE_WORLDWIDE = "worldwide_remote"
REMOTE_US_ONLY = "us_only"
REMOTE_UNKNOWN = "unknown"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key         TEXT UNIQUE NOT NULL,
    name              TEXT NOT NULL,
    domain            TEXT NOT NULL DEFAULT '',
    website           TEXT NOT NULL DEFAULT '',
    careers_url       TEXT NOT NULL DEFAULT '',
    ats_type          TEXT NOT NULL DEFAULT '',
    ats_token         TEXT NOT NULL DEFAULT '',
    industry          TEXT NOT NULL DEFAULT '',
    product           TEXT NOT NULL DEFAULT '',
    headquarters      TEXT NOT NULL DEFAULT '',
    office_locations  TEXT NOT NULL DEFAULT '[]',
    mumbai_office     INTEGER,               -- NULL = unknown
    mumbai_engineering INTEGER,              -- NULL = unknown
    remote_policy     TEXT NOT NULL DEFAULT 'unknown',
    company_size      TEXT NOT NULL DEFAULT 'unknown',
    funding_stage     TEXT NOT NULL DEFAULT 'unknown',
    technologies      TEXT NOT NULL DEFAULT '[]',
    sources           TEXT NOT NULL DEFAULT '[]',
    last_verified     TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT '',
    score             REAL NOT NULL DEFAULT 0,
    score_reasons     TEXT NOT NULL DEFAULT '[]',
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_category ON companies(category);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
"""

_COLUMNS = (
    "dedup_key", "name", "domain", "website", "careers_url", "ats_type",
    "ats_token", "industry", "product", "headquarters", "office_locations",
    "mumbai_office", "mumbai_engineering", "remote_policy", "company_size",
    "funding_stage", "technologies", "sources", "last_verified", "category",
    "score", "score_reasons", "first_seen_at", "last_seen_at",
)


def normalize_domain(url_or_domain: str) -> str:
    """Lowercase, strip scheme/path/www, return a bare domain."""
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    host = urlparse(s).netloc or urlparse(s).path
    host = host.split("@")[-1].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return host


def make_company_key(name: str, domain: str) -> str:
    import hashlib
    from .models import normalize_company
    d = normalize_domain(domain)
    joined = "|".join([normalize_company(name), d])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class Company:
    name: str
    website: str = ""
    careers_url: str = ""
    ats_type: str = ""
    ats_token: str = ""
    industry: str = ""
    product: str = ""
    headquarters: str = ""
    office_locations: List[str] = field(default_factory=list)
    mumbai_office: Optional[bool] = None
    mumbai_engineering: Optional[bool] = None
    remote_policy: str = REMOTE_UNKNOWN
    company_size: str = "unknown"
    funding_stage: str = "unknown"
    technologies: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    last_verified: str = ""
    category: str = ""
    score: float = 0.0
    score_reasons: List[dict] = field(default_factory=list)

    @property
    def domain(self) -> str:
        return normalize_domain(self.website)

    @property
    def dedup_key(self) -> str:
        return make_company_key(self.name, self.domain)

    def to_row(self, now: str) -> dict:
        return {
            "dedup_key": self.dedup_key, "name": self.name, "domain": self.domain,
            "website": self.website, "careers_url": self.careers_url,
            "ats_type": self.ats_type, "ats_token": self.ats_token,
            "industry": self.industry, "product": self.product,
            "headquarters": self.headquarters,
            "office_locations": json.dumps(self.office_locations),
            "mumbai_office": self.mumbai_office,
            "mumbai_engineering": self.mumbai_engineering,
            "remote_policy": self.remote_policy, "company_size": self.company_size,
            "funding_stage": self.funding_stage,
            "technologies": json.dumps(self.technologies),
            "sources": json.dumps(self.sources),
            "last_verified": self.last_verified, "category": self.category,
            "score": self.score, "score_reasons": json.dumps(self.score_reasons),
            "first_seen_at": now, "last_seen_at": now,
        }


class CompanySource(ABC):
    """Interface for a permitted company-discovery source."""

    source_type: str = ""

    @abstractmethod
    def fetch(self) -> List[Company]:
        raise NotImplementedError


class CompanyStorage:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def close(self) -> None:
        self.conn.close()

    def upsert(self, companies: List[Company]) -> int:
        """Insert or merge company records. Returns number newly inserted."""
        now = self._now()
        new = 0
        for c in companies:
            row = c.to_row(now)
            existing = self.conn.execute(
                "SELECT id, sources, office_locations, technologies, mumbai_office, "
                "mumbai_engineering, remote_policy, company_size, funding_stage, "
                "last_verified FROM companies WHERE dedup_key = ?",
                (c.dedup_key,),
            ).fetchone()
            if existing is None:
                values = [row[k] for k in _COLUMNS]
                self.conn.execute(
                    f"INSERT OR IGNORE INTO companies ({', '.join(_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _COLUMNS)})", values,
                )
                new += 1
            else:
                merged = self._merge(existing, c)
                self.conn.execute(
                    "UPDATE companies SET name=?, domain=?, website=?, careers_url=?, "
                    "ats_type=?, ats_token=?, industry=?, product=?, headquarters=?, "
                    "office_locations=?, mumbai_office=?, mumbai_engineering=?, "
                    "remote_policy=?, company_size=?, funding_stage=?, technologies=?, "
                    "sources=?, last_verified=?, category=?, score=?, score_reasons=?, "
                    "last_seen_at=? WHERE dedup_key=?",
                    (c.name, c.domain, c.website, c.careers_url, c.ats_type, c.ats_token,
                     c.industry, c.product, c.headquarters,
                     json.dumps(merged["office_locations"]),
                     c.mumbai_office if c.mumbai_office is not None else existing["mumbai_office"],
                     c.mumbai_engineering if c.mumbai_engineering is not None else existing["mumbai_engineering"],
                     c.remote_policy if c.remote_policy != REMOTE_UNKNOWN else existing["remote_policy"],
                     c.company_size if c.company_size != "unknown" else existing["company_size"],
                     c.funding_stage if c.funding_stage != "unknown" else existing["funding_stage"],
                     json.dumps(merged["technologies"]),
                     json.dumps(merged["sources"]),
                     c.last_verified or existing["last_verified"],
                     c.category, c.score, json.dumps(c.score_reasons),
                     now, c.dedup_key),
                )
        self.conn.commit()
        return new

    def _merge(self, existing, c: Company) -> dict:
        ex_off = json.loads(existing["office_locations"] or "[]")
        ex_tech = json.loads(existing["technologies"] or "[]")
        ex_src = json.loads(existing["sources"] or "[]")
        return {
            "office_locations": list(dict.fromkeys(ex_off + c.office_locations)),
            "technologies": list(dict.fromkeys(ex_tech + c.technologies)),
            "sources": list(dict.fromkeys(ex_src + c.sources)),
        }

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"])

    def deduplicate(self) -> int:
        """Merge rows sharing a domain (stale-key duplicates from a key change)."""
        rows = self.conn.execute(
            "SELECT id, name, domain, sources, office_locations, technologies, "
            "ats_type, ats_token, mumbai_office, remote_policy, funding_stage "
            "FROM companies WHERE domain != '' ORDER BY id").fetchall()
        seen = {}
        removed = 0
        for r in rows:
            if r["domain"] in seen:
                keep_id = seen[r["domain"]]
                self._merge_into(keep_id, r)
                self.conn.execute("DELETE FROM companies WHERE id = ?", (r["id"],))
                removed += 1
            else:
                seen[r["domain"]] = r["id"]
        self.conn.commit()
        return removed

    def _merge_into(self, keep_id: int, dup) -> None:
        import json as _json
        keep = self.conn.execute(
            "SELECT sources, office_locations, technologies, ats_type, ats_token, "
            "mumbai_office, remote_policy, funding_stage FROM companies WHERE id = ?",
            (keep_id,)).fetchone()
        src = list(dict.fromkeys(_json.loads(keep["sources"] or "[]") + _json.loads(dup["sources"] or "[]")))
        off = list(dict.fromkeys(_json.loads(keep["office_locations"] or "[]") + _json.loads(dup["office_locations"] or "[]")))
        tech = list(dict.fromkeys(_json.loads(keep["technologies"] or "[]") + _json.loads(dup["technologies"] or "[]")))
        ats_type = keep["ats_type"] or dup["ats_type"]
        ats_token = keep["ats_token"] or dup["ats_token"]
        mumbai = keep["mumbai_office"] if keep["mumbai_office"] is not None else dup["mumbai_office"]
        remote = keep["remote_policy"] if keep["remote_policy"] != "unknown" else dup["remote_policy"]
        funding = keep["funding_stage"] if keep["funding_stage"] != "unknown" else dup["funding_stage"]
        self.conn.execute(
            "UPDATE companies SET sources=?, office_locations=?, technologies=?, "
            "ats_type=?, ats_token=?, mumbai_office=?, remote_policy=?, funding_stage=? "
            "WHERE id=?",
            (_json.dumps(src), _json.dumps(off), _json.dumps(tech),
             ats_type, ats_token, mumbai, remote, funding, keep_id))

    def list(self, category: Optional[str] = None, mumbai: Optional[bool] = None,
             remote: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        where, params = [], []
        if category:
            where.append("category = ?")
            params.append(category)
        if mumbai is not None:
            where.append("mumbai_office = ?")
            params.append(1 if mumbai else 0)
        if remote:
            where.append("remote_policy = ?")
            params.append(remote)
        sql = "SELECT * FROM companies"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY score DESC, name ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def stats(self) -> dict:
        out: dict = {"total": self.count()}
        for field in ("category", "remote_policy"):
            rows = self.conn.execute(
                f"SELECT {field} k, COUNT(*) c FROM companies GROUP BY {field}"
            ).fetchall()
            out[field] = {r["k"] or "unset": r["c"] for r in rows}
        out["mumbai_office"] = int(self.conn.execute(
            "SELECT COUNT(*) c FROM companies WHERE mumbai_office = 1").fetchone()["c"])
        out["india_remote"] = int(self.conn.execute(
            "SELECT COUNT(*) c FROM companies WHERE remote_policy IN (?, ?)",
            (REMOTE_INDIA, REMOTE_WORLDWIDE)).fetchone()["c"])
        return out
