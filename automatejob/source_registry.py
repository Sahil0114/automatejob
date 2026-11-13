"""Source Registry: one consistent, honest record per discovery source.

The registry distinguishes two orthogonal facts about every source:

* ``status``          — operational state we control: active / paused / unsupported.
* ``integration_status`` — the *permitted* access method, one of the ten
  canonical values in ``IntegrationStatus``.

A registered source is not necessarily automated. A source we cannot legally or
technically automate is registered with an honest ``integration_status``
(``manual_import_only``, ``requires_permission``, ``email_alert_ingestion`` or
``unsupported``) rather than being omitted or silently marked active.

Credentials are never stored here — only a reference (env var name or file
path) and a note of where the secret lives.
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


class IntegrationStatus:
    """Canonical, permitted integration statuses (see phase spec)."""
    ACTIVE_API = "active_api"
    ACTIVE_RSS = "active_rss"
    ACTIVE_PUBLIC_SCRAPE = "active_public_scrape"
    ACTIVE_AUTHORIZED_PROVIDER = "active_authorized_provider"
    BROWSER_USER_DIRECTED = "browser_user_directed"
    EMAIL_ALERT_INGESTION = "email_alert_ingestion"
    MANUAL_IMPORT_ONLY = "manual_import_only"
    REQUIRES_PERMISSION = "requires_permission"
    UNSUPPORTED = "unsupported"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"

    ALL = (
        ACTIVE_API, ACTIVE_RSS, ACTIVE_PUBLIC_SCRAPE, ACTIVE_AUTHORIZED_PROVIDER,
        BROWSER_USER_DIRECTED, EMAIL_ALERT_INGESTION, MANUAL_IMPORT_ONLY,
        REQUIRES_PERMISSION, UNSUPPORTED, TEMPORARILY_UNAVAILABLE,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_registry (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT UNIQUE NOT NULL,
    source_type           TEXT NOT NULL DEFAULT '',
    source_category       TEXT NOT NULL DEFAULT '',
    access_method         TEXT NOT NULL DEFAULT '',
    classification        TEXT NOT NULL DEFAULT '',  -- api/rss/browser/manual/provider
    integration_status    TEXT NOT NULL DEFAULT 'unsupported',
    website               TEXT NOT NULL DEFAULT '',
    source_url            TEXT NOT NULL DEFAULT '',
    terms_url             TEXT NOT NULL DEFAULT '',
    api_feed_info         TEXT NOT NULL DEFAULT '',
    auth_required         INTEGER NOT NULL DEFAULT 0,
    auth_notes            TEXT NOT NULL DEFAULT '',
    permission            TEXT NOT NULL DEFAULT 'unknown',  -- authorized/unauthorized/unknown
    credential_reference  TEXT NOT NULL DEFAULT '',  -- env var / file path only, never the secret
    refresh_policy        TEXT NOT NULL DEFAULT '',
    rate_limit            TEXT NOT NULL DEFAULT '',
    provider_cost         TEXT NOT NULL DEFAULT '',
    firecrawl_credit_cost TEXT NOT NULL DEFAULT '',
    manual_workflow       TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'unsupported',
    reason                TEXT NOT NULL DEFAULT '',
    last_successful_at    TEXT NOT NULL DEFAULT '',
    last_verified_at      TEXT NOT NULL DEFAULT '',
    last_verified_records INTEGER NOT NULL DEFAULT 0,
    record_count          INTEGER NOT NULL DEFAULT 0,
    error_count           INTEGER NOT NULL DEFAULT 0,
    updated_at            TEXT NOT NULL
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
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the original schema (safe on existing DBs)."""
        existing = {r["name"] for r in self.conn.execute(
            "PRAGMA table_info(source_registry)").fetchall()}
        additions = {
            "source_category": "TEXT NOT NULL DEFAULT ''",
            "integration_status": "TEXT NOT NULL DEFAULT 'unsupported'",
            "website": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "terms_url": "TEXT NOT NULL DEFAULT ''",
            "api_feed_info": "TEXT NOT NULL DEFAULT ''",
            "auth_notes": "TEXT NOT NULL DEFAULT ''",
            "provider_cost": "TEXT NOT NULL DEFAULT ''",
            "firecrawl_credit_cost": "TEXT NOT NULL DEFAULT ''",
            "manual_workflow": "TEXT NOT NULL DEFAULT ''",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "last_verified_at": "TEXT NOT NULL DEFAULT ''",
            "record_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for col, decl in additions.items():
            if col not in existing:
                self.conn.execute(
                    f"ALTER TABLE source_registry ADD COLUMN {col} {decl}")

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def upsert(self, name: str, source_type: str, *, source_category: str = "",
               access: str = "", classification: str = "",
               integration_status: str = IntegrationStatus.UNSUPPORTED,
               website: str = "", source_url: str = "", terms_url: str = "",
               api_feed_info: str = "", auth_required: bool = False,
               auth_notes: str = "", permission: str = "unknown",
               credential_reference: str = "", refresh_policy: str = "",
               rate_limit: str = "", provider_cost: str = "",
               firecrawl_credit_cost: str = "", manual_workflow: str = "",
               status: str = SourceStatus.UNSUPPORTED, reason: str = "") -> int:
        """Insert or update a source record (all metadata preserved on update)."""
        if status not in (SourceStatus.ACTIVE, SourceStatus.PAUSED, SourceStatus.UNSUPPORTED):
            raise ValueError(f"invalid source status {status!r}")
        if integration_status not in IntegrationStatus.ALL:
            raise ValueError(f"invalid integration status {integration_status!r}")
        existing = self.conn.execute(
            "SELECT id FROM source_registry WHERE name=?", (name,)).fetchone()
        cols = ("source_type", "source_category", "access_method", "classification",
                "integration_status", "website", "source_url", "terms_url",
                "api_feed_info", "auth_required", "auth_notes", "permission",
                "credential_reference", "refresh_policy", "rate_limit",
                "provider_cost", "firecrawl_credit_cost", "manual_workflow",
                "status", "reason", "updated_at")
        vals = (source_type, source_category, access, classification,
                integration_status, website, source_url, terms_url,
                api_feed_info, int(auth_required), auth_notes, permission,
                credential_reference, refresh_policy, rate_limit,
                provider_cost, firecrawl_credit_cost, manual_workflow,
                status, reason, _now())
        if existing:
            assignments = ", ".join(f"{c}=?" for c in cols)
            self.conn.execute(
                f"UPDATE source_registry SET {assignments} WHERE name=?",
                vals + (name,))
            sid = existing["id"]
        else:
            self.conn.execute(
                f"INSERT INTO source_registry (name, {', '.join(cols)}) VALUES "
                f"({', '.join('?' for _ in cols + ('name',))})", (name,) + vals)
            sid = int(self.conn.execute(
                "SELECT id FROM source_registry WHERE name=?", (name,)).fetchone()["id"])
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

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM source_registry").fetchone()["c"])

    def record_success(self, source_id: int, verified_records: int = 0) -> None:
        now = _now()
        self.conn.execute(
            "UPDATE source_registry SET last_successful_at=?, last_verified_at=?, "
            "last_verified_records=?, record_count=?, updated_at=? WHERE id=?",
            (now, now, int(verified_records), int(verified_records), now, source_id))
        self.conn.commit()

    def record_error(self, source_id: int, note: str = "") -> None:
        self.conn.execute(
            "UPDATE source_registry SET error_count=error_count+1, reason=?, "
            "updated_at=? WHERE id=?", (note, _now(), source_id))
        self.conn.commit()

    def set_status(self, source_id: int, status: str, reason: str = "") -> bool:
        if status not in (SourceStatus.ACTIVE, SourceStatus.PAUSED, SourceStatus.UNSUPPORTED):
            raise ValueError(f"invalid source status {status!r}")
        cur = self.conn.execute(
            "UPDATE source_registry SET status=?, reason=?, updated_at=? WHERE id=?",
            (status, reason, _now(), source_id))
        self.conn.commit()
        return cur.rowcount > 0


# The 19 requested discovery sources plus the already-integrated operational
# sources and Alexandria providers. Each record is honest: a source we cannot
# legally/technically automate is registered as manual_import_only /
# requires_permission / unsupported, never fabricated as active.
SOURCE_CATALOG = [
    # name, source_type, category, integration_status, auth_required, permission, status, reason, ...
    {
        "name": "greenhouse", "source_type": "greenhouse",
        "source_category": "ats_job_board",
        "integration_status": IntegrationStatus.ACTIVE_API,
        "website": "https://www.greenhouse.com",
        "source_url": "https://boards.greenhouse.io/{token}",
        "terms_url": "https://www.greenhouse.com/terms-of-service",
        "api_feed_info": "Public job-board JSON API per company token",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "incremental",
        "rate_limit": "board rate limit", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "api", "access": "api",
    },
    {
        "name": "lever", "source_type": "lever",
        "source_category": "ats_job_board",
        "integration_status": IntegrationStatus.ACTIVE_API,
        "website": "https://www.lever.co",
        "source_url": "https://api.lever.co/v0/postings/{token}",
        "terms_url": "https://www.lever.co/terms",
        "api_feed_info": "Public JSON postings API per company token",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "incremental",
        "rate_limit": "board rate limit", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "api", "access": "api",
    },
    {
        "name": "ashby", "source_type": "ashby",
        "source_category": "ats_job_board",
        "integration_status": IntegrationStatus.ACTIVE_API,
        "website": "https://www.ashbyhq.com",
        "source_url": "https://api.ashbyhq.com/posting-api/job-board/{token}",
        "terms_url": "https://www.ashbyhq.com/terms",
        "api_feed_info": "Public posting-api job-board JSON per company token",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "incremental",
        "rate_limit": "board rate limit", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "api", "access": "api",
    },
    {
        "name": "yc_waas", "source_type": "yc_waas",
        "source_category": "startup_job_board",
        "integration_status": IntegrationStatus.ACTIVE_PUBLIC_SCRAPE,
        "website": "https://www.workatastartup.com",
        "source_url": "https://www.workatastartup.com/companies/{slug}",
        "terms_url": "https://www.ycombinator.com/legal",
        "api_feed_info": "Server-rendered company pages with embedded JSON (data-page)",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "per-company incremental",
        "rate_limit": "bounded per-company", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "browser", "access": "public_scrape",
    },
    {
        "name": "hn", "source_type": "hn",
        "source_category": "community_job_board",
        "integration_status": IntegrationStatus.ACTIVE_API,
        "website": "https://news.ycombinator.com",
        "source_url": "https://hn.algolia.com/api/v1/",
        "terms_url": "https://news.ycombinator.com/newsguidelines.html",
        "api_feed_info": "Public Algolia HN API (search + items) for monthly Who-is-hiring",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "latest monthly thread",
        "rate_limit": "1 req/s recommended", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "api", "access": "api",
    },
    {
        "name": "simplifyjobs", "source_type": "simplifyjobs",
        "source_category": "curated_job_lists",
        "integration_status": IntegrationStatus.ACTIVE_PUBLIC_SCRAPE,
        "website": "https://github.com/SimplifyJobs",
        "source_url": "https://github.com/SimplifyJobs/{token}",
        "terms_url": "https://docs.github.com/en/site-policy",
        "api_feed_info": "Public GitHub repository README job lists",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "incremental",
        "rate_limit": "bounded", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a", "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "browser", "access": "public_scrape",
    },
    {
        "name": "yc_directory", "source_type": "yc_directory",
        "source_category": "company_directory",
        "integration_status": IntegrationStatus.ACTIVE_API,
        "website": "https://www.ycombinator.com/companies",
        "source_url": "https://api.ycombinator.com/v0.1/companies",
        "terms_url": "https://www.ycombinator.com/legal",
        "api_feed_info": "Public paginated YC company directory JSON API",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "resumable pagination",
        "rate_limit": "public, bounded", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "api", "access": "api",
    },
    {
        "name": "weworkremotely", "source_type": "weworkremotely",
        "source_category": "remote_job_board",
        "integration_status": IntegrationStatus.ACTIVE_RSS,
        "website": "https://weworkremotely.com",
        "source_url": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "terms_url": "https://weworkremotely.com/terms",
        "api_feed_info": "Public RSS feeds per category (remote-programming-jobs.rss)",
        "auth_required": False, "permission": "authorized",
        "credential_reference": "", "refresh_policy": "incremental RSS",
        "rate_limit": "polite poll", "provider_cost": "0",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "rss", "access": "rss",
    },
    {
        "name": "firecrawl", "source_type": "firecrawl",
        "source_category": "web_discovery_tool",
        "integration_status": IntegrationStatus.ACTIVE_AUTHORIZED_PROVIDER,
        "website": "https://www.firecrawl.dev",
        "source_url": "https://www.firecrawl.dev",
        "terms_url": "https://www.firecrawl.dev/terms",
        "api_feed_info": "Firecrawl CLI (search/scrape/map/crawl) + Alexandria providers",
        "auth_required": True, "auth_notes": "Firecrawl CLI browser login / stored credential",
        "permission": "authorized", "credential_reference": "firecrawl-cli secure store",
        "refresh_policy": "on demand", "rate_limit": "account limit",
        "provider_cost": "varies", "firecrawl_credit_cost": "search 2/10 results; scrape/map 1; crawl 1/page",
        "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "provider", "access": "api",
    },
    {
        "name": "alexandria_yc", "source_type": "ycombinator-com",
        "source_category": "alexandria_provider",
        "integration_status": IntegrationStatus.ACTIVE_AUTHORIZED_PROVIDER,
        "website": "https://alexandria.firecrawl.dev/ycombinator-com",
        "source_url": "https://alexandria.firecrawl.dev/ycombinator-com",
        "terms_url": "https://www.firecrawl.dev/terms",
        "api_feed_info": "Alexandria YC company/job search tools",
        "auth_required": True, "auth_notes": "Firecrawl CLI credential",
        "permission": "authorized", "credential_reference": "firecrawl-cli secure store",
        "refresh_policy": "selective enrichment only", "rate_limit": "provider",
        "provider_cost": "5 credits/call", "firecrawl_credit_cost": "5/call",
        "manual_workflow": "n/a",
        "status": SourceStatus.ACTIVE, "reason": "",
        "classification": "provider", "access": "api",
    },
    {
        "name": "alexandria_blitzapi", "source_type": "blitzapi",
        "source_category": "alexandria_provider",
        "integration_status": IntegrationStatus.ACTIVE_AUTHORIZED_PROVIDER,
        "website": "https://alexandria.firecrawl.dev/blitzapi",
        "source_url": "https://alexandria.firecrawl.dev/blitzapi",
        "terms_url": "https://www.firecrawl.dev/terms",
        "api_feed_info": "BlitzAPI company/people/jobs tools (per-record charges)",
        "auth_required": True, "auth_notes": "Firecrawl CLI credential",
        "permission": "authorized", "credential_reference": "firecrawl-cli secure store",
        "refresh_policy": "on demand", "rate_limit": "provider",
        "provider_cost": "2 credits/call base; +10 credits per billed record on several tools",
        "firecrawl_credit_cost": "varies (per-record)",
        "manual_workflow": "manual Apollo email entry instead",
        "status": SourceStatus.PAUSED,
        "reason": "per-record charges; requires explicit per-call cost approval",
        "classification": "provider", "access": "api",
    },
    {
        "name": "alexandria_harvestapi", "source_type": "harvestapi",
        "source_category": "alexandria_provider",
        "integration_status": IntegrationStatus.ACTIVE_AUTHORIZED_PROVIDER,
        "website": "https://alexandria.firecrawl.dev/harvestapi",
        "source_url": "https://alexandria.firecrawl.dev/harvestapi",
        "terms_url": "https://www.firecrawl.dev/terms",
        "api_feed_info": "HarvestAPI (recruiter/LinkedIn post search) — capability unverified",
        "auth_required": True, "auth_notes": "Firecrawl CLI credential",
        "permission": "authorized", "credential_reference": "firecrawl-cli secure store",
        "refresh_policy": "on demand", "rate_limit": "provider",
        "provider_cost": "2 credits/call (unverified)", "firecrawl_credit_cost": "unverified",
        "manual_workflow": "manual recruiter-post text/URL import",
        "status": SourceStatus.PAUSED,
        "reason": "no matching tools visible in Alexandria catalogue; capability unverified",
        "classification": "provider", "access": "api",
    },
    {
        "name": "apollo", "source_type": "apollo",
        "source_category": "contact_enrichment",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.apollo.io",
        "source_url": "https://www.apollo.io",
        "terms_url": "https://www.apollo.io/terms-of-service",
        "api_feed_info": "Apollo enrichment API (not authorized)",
        "auth_required": True, "auth_notes": "requires Apollo account + API key",
        "permission": "unauthorized", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "per-enrichment", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "find emails in Apollo manually, paste into dashboard",
        "status": SourceStatus.UNSUPPORTED,
        "reason": "primary workflow is manual Apollo email entry, not automated enrichment",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "linkedin", "source_type": "linkedin",
        "source_category": "professional_network",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.linkedin.com",
        "source_url": "https://www.linkedin.com",
        "terms_url": "https://www.linkedin.com/legal/user-agreement",
        "api_feed_info": "LinkedIn official API / authorized data providers (not authorized)",
        "auth_required": True, "auth_notes": "requires authorized API access; logged-in browser is NOT scraping permission",
        "permission": "unauthorized", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "paste LinkedIn post text or URL into recruiter-post pipeline; screenshots",
        "status": SourceStatus.UNSUPPORTED,
        "reason": "unauthorized scraping prohibited; manual import only",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "internshala", "source_type": "internshala",
        "source_category": "internship_portal",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://internshala.com",
        "source_url": "https://internshala.com",
        "terms_url": "https://internshala.com/terms",
        "api_feed_info": "no verified public API/feed; saved-search emails",
        "auth_required": True, "auth_notes": "requires account for saved searches; no authorized API",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if Gmail authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "no authorized automated access; manual import + email alerts",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "indeed", "source_type": "indeed",
        "source_category": "job_aggregator",
        "integration_status": IntegrationStatus.REQUIRES_PERMISSION,
        "website": "https://in.indeed.com",
        "source_url": "https://in.indeed.com",
        "terms_url": "https://www.indeed.com/legal",
        "api_feed_info": "Indeed Publisher API / job-alert email (not authorized)",
        "auth_required": True, "auth_notes": "requires publisher API or alert email; scraping prohibited",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "access restrictions must be reviewed; no authorized automated access",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "naukri", "source_type": "naukri",
        "source_category": "job_aggregator",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.naukri.com",
        "source_url": "https://www.naukri.com",
        "terms_url": "https://www.naukri.com/terms",
        "api_feed_info": "no verified public API/feed; saved-search emails",
        "auth_required": True, "auth_notes": "requires account for saved searches",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if Gmail authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "no authorized automated access; manual import + alerts",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "wellfound", "source_type": "wellfound",
        "source_category": "startup_job_board",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://wellfound.com",
        "source_url": "https://wellfound.com",
        "terms_url": "https://wellfound.com/terms",
        "api_feed_info": "no stable public API verified",
        "auth_required": True, "auth_notes": "no verified authorized API",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "no reliable permitted interface tested",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "foundit", "source_type": "foundit",
        "source_category": "job_aggregator",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.foundit.in",
        "source_url": "https://www.foundit.in",
        "terms_url": "https://www.foundit.in/terms",
        "api_feed_info": "no verified public API; saved-search emails",
        "auth_required": True, "auth_notes": "requires account for saved searches",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if Gmail authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "no authorized automated access",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "cutshort", "source_type": "cutshort",
        "source_category": "job_portal",
        "integration_status": IntegrationStatus.REQUIRES_PERMISSION,
        "website": "https://cutshort.io",
        "source_url": "https://cutshort.io",
        "terms_url": "https://cutshort.io/terms",
        "api_feed_info": "official API/MCP integrations (not authorized)",
        "auth_required": True, "auth_notes": "requires authorized API access",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "on permission", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import",
        "status": SourceStatus.PAUSED,
        "reason": "restricted account scraping prohibited; requires permission",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "hirist", "source_type": "hirist",
        "source_category": "tech_job_board",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.hirist.tech",
        "source_url": "https://www.hirist.tech",
        "terms_url": "https://www.hirist.tech/terms",
        "api_feed_info": "no verified public API/feed",
        "auth_required": False, "auth_notes": "public pages only",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / permitted alerts",
        "status": SourceStatus.PAUSED,
        "reason": "no verified authorized access",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "uplers", "source_type": "uplers",
        "source_category": "remote_job_board",
        "integration_status": IntegrationStatus.ACTIVE_PUBLIC_SCRAPE,
        "website": "https://www.uplers.com",
        "source_url": "https://www.uplers.com",
        "terms_url": "https://www.uplers.com/terms",
        "api_feed_info": "public careers/job pages",
        "auth_required": False, "auth_notes": "public pages only",
        "permission": "authorized", "credential_reference": "",
        "refresh_policy": "bounded Firecrawl where permitted",
        "rate_limit": "bounded", "provider_cost": "0",
        "firecrawl_credit_cost": "scrape 1/page",
        "manual_workflow": "n/a",
        "status": SourceStatus.PAUSED,
        "reason": "public-scrape adapter not yet live-verified this phase",
        "classification": "browser", "access": "public_scrape",
    },
    {
        "name": "instahyre", "source_type": "instahyre",
        "source_category": "job_portal",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://www.instahyre.com",
        "source_url": "https://www.instahyre.com",
        "terms_url": "https://www.instahyre.com/terms",
        "api_feed_info": "no verified public API; saved-search emails",
        "auth_required": True, "auth_notes": "requires account for saved searches",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if Gmail authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "no authorized automated access",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "jobfound", "source_type": "jobfound",
        "source_category": "job_board",
        "integration_status": IntegrationStatus.UNSUPPORTED,
        "website": "https://jobfound.org",
        "source_url": "https://jobfound.org",
        "terms_url": "",
        "api_feed_info": "site identity/interface unverified",
        "auth_required": False, "auth_notes": "",
        "permission": "unknown", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import",
        "status": SourceStatus.UNSUPPORTED,
        "reason": "identity/functionality/interface not verified; no guessed endpoints",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "flexjobs", "source_type": "flexjobs",
        "source_category": "remote_job_board",
        "integration_status": IntegrationStatus.REQUIRES_PERMISSION,
        "website": "https://www.flexjobs.com",
        "source_url": "https://www.flexjobs.com",
        "terms_url": "https://www.flexjobs.com/terms",
        "api_feed_info": "no verified public API; saved searches/email notifications",
        "auth_required": True, "auth_notes": "requires account; restricted data",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "email alert ingestion (if authorized)",
        "rate_limit": "n/a", "provider_cost": "n/a",
        "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / job-alert email ingestion",
        "status": SourceStatus.PAUSED,
        "reason": "restricted account data; no scraping",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "gethnjobs", "source_type": "gethnjobs",
        "source_category": "job_aggregator",
        "integration_status": IntegrationStatus.UNSUPPORTED,
        "website": "https://gethnjobs.com",
        "source_url": "https://gethnjobs.com",
        "terms_url": "",
        "api_feed_info": "identity/functionality unverified",
        "auth_required": False, "auth_notes": "",
        "permission": "unknown", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import",
        "status": SourceStatus.UNSUPPORTED,
        "reason": "site identity/functionality unverified; no assumed endpoints",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "devopshunt", "source_type": "devopshunt",
        "source_category": "tech_job_board",
        "integration_status": IntegrationStatus.REQUIRES_PERMISSION,
        "website": "https://devopshunt.com",
        "source_url": "https://devopshunt.com",
        "terms_url": "",
        "api_feed_info": "official MCP/API access (not authorized)",
        "auth_required": True, "auth_notes": "requires API/MCP credentials and pricing confirmation",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "on permission", "rate_limit": "n/a",
        "provider_cost": "unverified", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import",
        "status": SourceStatus.PAUSED,
        "reason": "requires API/MCP access + pricing/terms verification",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "theantijobboard", "source_type": "theantijobboard",
        "source_category": "job_board",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://theantijobboard.com",
        "source_url": "https://theantijobboard.com",
        "terms_url": "",
        "api_feed_info": "no verified public API; export options unverified",
        "auth_required": False, "auth_notes": "",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "n/a",
        "manual_workflow": "manual import / supported export",
        "status": SourceStatus.PAUSED,
        "reason": "terms/export unverified; manual import preferred",
        "classification": "manual", "access": "manual",
    },
    {
        "name": "x", "source_type": "x",
        "source_category": "social_hiring_announcements",
        "integration_status": IntegrationStatus.MANUAL_IMPORT_ONLY,
        "website": "https://x.com",
        "source_url": "https://x.com",
        "terms_url": "https://x.com/en/tos",
        "api_feed_info": "official X API / authorized data provider (not authorized)",
        "auth_required": True, "auth_notes": "requires official X API access or authorized provider",
        "permission": "requires_permission", "credential_reference": "",
        "refresh_policy": "manual", "rate_limit": "n/a",
        "provider_cost": "n/a", "firecrawl_credit_cost": "variable",
        "manual_workflow": "paste X post URL/text into recruiter-post pipeline",
        "status": SourceStatus.PAUSED,
        "reason": "no authorized API access; expensive X searches not run without approval",
        "classification": "manual", "access": "manual",
    },
]


def seed_defaults(reg: SourceRegistry) -> int:
    """Seed the canonical catalog into the registry (idempotent by name)."""
    for item in SOURCE_CATALOG:
        reg.upsert(
            item["name"], item["source_type"],
            source_category=item.get("source_category", ""),
            access=item.get("access", ""),
            classification=item.get("classification", ""),
            integration_status=item.get("integration_status", IntegrationStatus.UNSUPPORTED),
            website=item.get("website", ""),
            source_url=item.get("source_url", ""),
            terms_url=item.get("terms_url", ""),
            api_feed_info=item.get("api_feed_info", ""),
            auth_required=item.get("auth_required", False),
            auth_notes=item.get("auth_notes", ""),
            permission=item.get("permission", "unknown"),
            credential_reference=item.get("credential_reference", ""),
            refresh_policy=item.get("refresh_policy", ""),
            rate_limit=item.get("rate_limit", ""),
            provider_cost=item.get("provider_cost", ""),
            firecrawl_credit_cost=item.get("firecrawl_credit_cost", ""),
            manual_workflow=item.get("manual_workflow", ""),
            status=item.get("status", SourceStatus.UNSUPPORTED),
            reason=item.get("reason", ""),
        )
    return reg.count()
