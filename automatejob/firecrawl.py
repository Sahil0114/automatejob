"""Optional Firecrawl CLI integration with conservative local cost controls.

The Python application never stores a Firecrawl key. The official CLI handles
browser login/secure credential storage; this module calls that CLI only after a
budget reservation. All ledger amounts are estimates until an authenticated
account usage response is available.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class BudgetExceeded(RuntimeError):
    """Raised before an operation that would cross the configured hard limit."""


@dataclass
class FirecrawlConfig:
    enabled: bool = True
    monthly_budget: int = 1000
    soft_limit: int = 700
    hard_limit: int = 800
    absolute_limit: int = 1000
    search_results_default: int = 10
    db_path: str = "data/firecrawl.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS firecrawl_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    estimated_credits REAL NOT NULL DEFAULT 0,
    actual_credits REAL,
    status TEXT NOT NULL DEFAULT 'completed',
    request_key TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_firecrawl_ops_at ON firecrawl_operations(at);
CREATE INDEX IF NOT EXISTS idx_firecrawl_ops_endpoint ON firecrawl_operations(endpoint, provider);
CREATE TABLE IF NOT EXISTS firecrawl_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    expires_at TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _day_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class FirecrawlBudget:
    """SQLite ledger + mutex-like reservation performed through BEGIN IMMEDIATE."""

    def __init__(self, db_path: str, config: FirecrawlConfig):
        self.config = config
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def estimate(self, endpoint: str, *, results: int = 0, pages: int = 0,
                 provider: str = "", provider_cost: Optional[float] = None) -> float:
        """Return the configured conservative estimate.

        Alexandria pricing is provider-specific; an unknown price is rejected
        rather than guessed. Callers must inspect the provider contract first.
        """
        endpoint = endpoint.lower()
        if endpoint == "search":
            return float(2 * max(1, math.ceil((results or self.config.search_results_default) / 10)))
        if endpoint in ("scrape", "map", "crawl"):
            return float(max(1, pages or 1))
        if endpoint == "interact":
            if provider_cost is None:
                raise ValueError("interact cost must be supplied from the selected mode/session")
            return float(provider_cost)
        if endpoint == "alexandria":
            if not provider or provider_cost is None:
                raise ValueError("Alexandria provider pricing must be explicitly supplied from its contract")
            return float(provider_cost)
        raise ValueError(f"unsupported Firecrawl endpoint {endpoint!r}")

    def _consumed(self, prefix: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(actual_credits, estimated_credits)),0) AS total "
            "FROM firecrawl_operations WHERE at LIKE ? AND status IN ('reserved','completed','unknown')",
            (prefix + "%",)).fetchone()
        return float(row["total"])

    def reserve(self, endpoint: str, *, estimated_credits: float, provider: str = "",
                request_key: str = "", note: str = "") -> int:
        """Reserve credits atomically before a CLI request starts."""
        if not self.config.enabled:
            raise BudgetExceeded("Firecrawl is disabled in configuration")
        limit = min(self.config.monthly_budget, self.config.hard_limit, self.config.absolute_limit)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            consumed = self._consumed(_month_prefix())
            if consumed + estimated_credits > limit:
                self.conn.rollback()
                raise BudgetExceeded(
                    f"Firecrawl hard limit {limit:g} would be exceeded: "
                    f"{consumed:g} + {estimated_credits:g}")
            cur = self.conn.execute(
                "INSERT INTO firecrawl_operations (at,endpoint,provider,estimated_credits,status,request_key,note) "
                "VALUES (?,?,?,?,?,?,?)",
                (_now(), endpoint, provider, estimated_credits, "reserved", request_key, note))
            self.conn.commit()
            return int(cur.lastrowid or 0)
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def complete(self, operation_id: int, *, actual_credits: Optional[float] = None,
                 status: str = "completed", note: str = "") -> None:
        if status not in ("completed", "failed", "unknown"):
            raise ValueError("invalid Firecrawl operation status")
        self.conn.execute(
            "UPDATE firecrawl_operations SET actual_credits=?, status=?, note=? WHERE id=?",
            (actual_credits, status, note, operation_id))
        self.conn.commit()

    def record(self, endpoint: str, *, provider: str = "", estimated_credits: float = 0,
               actual_credits: Optional[float] = None, status: str = "completed",
               request_key: str = "", note: str = "") -> int:
        """Record an already-completed/known operation (used in tests and imports)."""
        cur = self.conn.execute(
            "INSERT INTO firecrawl_operations (at,endpoint,provider,estimated_credits,actual_credits,status,request_key,note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_now(), endpoint, provider, estimated_credits, actual_credits,
             status, request_key, note))
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def usage(self) -> dict:
        monthly = self._consumed(_month_prefix())
        daily = self._consumed(_day_prefix())
        limit = min(self.config.monthly_budget, self.config.hard_limit, self.config.absolute_limit)
        endpoint_rows = self.conn.execute(
            "SELECT endpoint, COALESCE(SUM(COALESCE(actual_credits,estimated_credits)),0) total "
            "FROM firecrawl_operations WHERE at LIKE ? AND status IN ('reserved','completed','unknown') "
            "GROUP BY endpoint", (_month_prefix() + "%",)).fetchall()
        provider_rows = self.conn.execute(
            "SELECT provider, COALESCE(SUM(COALESCE(actual_credits,estimated_credits)),0) total "
            "FROM firecrawl_operations WHERE at LIKE ? AND provider != '' "
            "AND status IN ('reserved','completed','unknown') GROUP BY provider",
            (_month_prefix() + "%",)).fetchall()
        return {
            "monthly_consumed": monthly,
            "daily_consumed": daily,
            "budget_limit": limit,
            "budget_remaining": max(0.0, limit - monthly),
            "soft_limit": self.config.soft_limit,
            "paused": monthly >= limit,
            "estimated": True,
            "by_endpoint": {r["endpoint"]: float(r["total"]) for r in endpoint_rows},
            "by_provider": {r["provider"]: float(r["total"]) for r in provider_rows},
        }

    def put_cache(self, key: str, payload: dict, expires_at: str = "") -> None:
        self.conn.execute(
            "INSERT INTO firecrawl_cache (cache_key,payload,stored_at,expires_at) VALUES (?,?,?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, stored_at=excluded.stored_at, "
            "expires_at=excluded.expires_at", (key, json.dumps(payload), _now(), expires_at))
        self.conn.commit()

    def get_cache(self, key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT payload,expires_at FROM firecrawl_cache WHERE cache_key=?", (key,)).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] <= _now():
            return None
        return json.loads(row["payload"])


class FirecrawlCLI:
    """Thin wrapper around the official ``firecrawl`` CLI.

    The runner is injectable for tests; production uses ``subprocess.run`` so no
    Firecrawl key is ever stored by this module. Every mutating network call is
    preceded by a budget reservation and followed by ledger completion.
    """

    def __init__(self, budget: FirecrawlBudget, runner=None, timeout: int = 120):
        self.budget = budget
        self.runner = runner or self._run
        self.timeout = timeout

    @staticmethod
    def _run(args, **kwargs):
        return subprocess.run(args, capture_output=True, text=True, timeout=kwargs.pop("timeout", 120))

    @staticmethod
    def _cache_key(endpoint: str, payload: dict) -> str:
        return endpoint + ":" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _invoke(self, args: list, *, endpoint: str, provider: str = "",
                estimated_credits: float, request_key: str = "", note: str = "") -> dict:
        """Reserve, invoke, complete, and return parsed JSON (never raw text)."""
        op_id = self.budget.reserve(endpoint, estimated_credits=estimated_credits,
                                    provider=provider, request_key=request_key, note=note)
        result = self.runner(args, timeout=self.timeout)
        ok = getattr(result, "returncode", 0) == 0
        payload = None
        if ok:
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                ok = False
        stderr = getattr(result, "stderr", "") or ""
        actual = None
        failure_status = "unknown"
        # The CLI sometimes reports a definitive charge even when a provider
        # returns an error. Use that exact amount; otherwise reserve the
        # estimate as unknown so a retry cannot silently exceed the budget.
        charged = re.search(r"(?im)^credits:\s*([0-9]+(?:\.[0-9]+)?)\s*$", stderr)
        if charged:
            actual = float(charged.group(1))
            failure_status = "failed"
        self.budget.complete(op_id, actual_credits=actual,
                             status="completed" if ok else failure_status,
                             note="" if ok else stderr[:200])
        if not ok:
            raise RuntimeError(f"Firecrawl {endpoint} failed: "
                               f"{stderr[:200]}")
        return payload

    def auth_status(self) -> dict:
        """Non-billed local check of CLI auth + credits (no cache/ledger)."""
        result = self.runner(["firecrawl", "status"], timeout=30)
        text = (getattr(result, "stdout", "") or "").lower()
        return {
            "authenticated": "not authenticated" not in text,
            "raw": "authenticated" if "not authenticated" not in text else "not authenticated",
        }

    def account_usage(self) -> dict:
        """Authoritative account usage from the CLI when available."""
        result = self.runner(["firecrawl", "credit-usage", "--json"], timeout=30)
        if getattr(result, "returncode", 1) != 0:
            return {"source": "local_ledger_estimated", "available": False,
                    **self.budget.usage()}
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return {"source": "local_ledger_estimated", "available": False,
                    **self.budget.usage()}
        # CLI shape: {"success": true, "data": {"remainingCredits": N, ...}}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict) or not any(k in data for k in ("remaining", "remainingCredits")):
            return {"source": "local_ledger_estimated", "available": False,
                    **self.budget.usage()}
        return {
            "source": "firecrawl_cli",
            "available": True,
            "remaining": data.get("remaining", data.get("remainingCredits")),
            "used": data.get("used", data.get("usedCredits")),
            "plan": data.get("plan", data.get("planCredits", "")),
        }

    def search(self, query: str, *, limit: int = 10, sources: str = "web") -> dict:
        key = self._cache_key("search", {"q": query, "limit": limit, "sources": sources})
        cached = self.budget.get_cache(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out
        payload = self._invoke(
            ["firecrawl", "search", query, "--limit", str(limit), "--sources", sources, "--json"],
            endpoint="search", estimated_credits=self.budget.estimate("search", results=limit),
            request_key=key, note=query[:120])
        self.budget.put_cache(key, payload)
        payload["cached"] = False
        return payload

    def scrape(self, url: str, *, formats: str = "markdown") -> dict:
        key = self._cache_key("scrape", {"url": url, "formats": formats})
        cached = self.budget.get_cache(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out
        payload = self._invoke(
            ["firecrawl", "scrape", url, "--format", formats, "--json"],
            endpoint="scrape", estimated_credits=1,
            request_key=key, note=url[:160])
        self.budget.put_cache(key, payload)
        payload["cached"] = False
        return payload

    def map(self, url: str, *, search: str = "") -> dict:
        args = ["firecrawl", "map", url, "--json"]
        if search:
            args += ["--search", search]
        key = self._cache_key("map", {"url": url, "search": search})
        cached = self.budget.get_cache(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out
        payload = self._invoke(args, endpoint="map", estimated_credits=1,
                               request_key=key, note=url[:160])
        self.budget.put_cache(key, payload)
        payload["cached"] = False
        return payload

    def crawl(self, url: str, *, limit: int = 10, max_depth: int = 1,
              path_pattern: str = "") -> dict:
        """Bounded crawl: explicit page limit, depth and optional path prefix."""
        args = ["firecrawl", "crawl", url, "--limit", str(limit), "--json"]
        if max_depth:
            args += ["--max-depth", str(max_depth)]
        if path_pattern:
            args += ["--path", path_pattern]
        return self._invoke(args, endpoint="crawl",
                            estimated_credits=self.budget.estimate("crawl", pages=limit),
                            request_key=self._cache_key("crawl", {"url": url, "limit": limit}),
                            note=url[:160])

    def alexandria(self, provider: str, capability: str, options: dict, *,
                   provider_cost: Optional[float] = None, cache_ttl_seconds: int = 0) -> dict:
        """Execute an Alexandria provider capability with explicit declared cost.

        ``provider_cost`` must come from the inspected provider contract — it is
        never guessed. Cache reuse is opt-in via ``cache_ttl_seconds``.
        """
        if provider_cost is None:
            raise ValueError("Alexandria provider cost must be declared explicitly")
        key = self._cache_key("alexandria", {"provider": provider, "capability": capability,
                                            "options": options})
        if cache_ttl_seconds:
            cached = self.budget.get_cache(key)
            if cached is not None:
                out = dict(cached)
                out["cached"] = True
                return out
        args = ["firecrawl", "scrape", f"{provider}/{capability}",
                "--options", json.dumps(options), "--json"]
        payload = self._invoke(args, endpoint="alexandria", provider=provider,
                               estimated_credits=float(provider_cost),
                               request_key=key, note=capability)
        if cache_ttl_seconds:
            self.budget.put_cache(key, payload)
        payload["cached"] = False
        return payload

