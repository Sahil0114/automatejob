"""Outreach pipeline: normalized database + state machine + follow-up logic.

Turns company/job discovery into a tracked pipeline of contacts, evidence,
email drafts, approvals, and follow-ups. History is append-only (status changes
record audit events, never overwrite). Nothing here sends email — drafts and
follow-ups require explicit approval.

States (company-level): discovered -> qualified -> researched -> contact_found
-> draft_ready -> awaiting_approval -> contacted -> replied -> interview,
plus independent rejection / manual_review / disqualified.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# Pipeline states.
DISCOVERED = "discovered"
QUALIFIED = "qualified"
RESEARCHED = "researched"
CONTACT_FOUND = "contact_found"
DRAFT_READY = "draft_ready"
AWAITING_APPROVAL = "awaiting_approval"
CONTACTED = "contacted"
REPLIED = "replied"
INTERVIEW = "interview"
# Terminal / side states.
REJECTED = "rejected"
MANUAL_REVIEW = "manual_review"
DISQUALIFIED = "disqualified"

COMPANY_STATES = (DISCOVERED, QUALIFIED, RESEARCHED, CONTACT_FOUND, DRAFT_READY,
                  AWAITING_APPROVAL, CONTACTED, REPLIED, INTERVIEW,
                  REJECTED, MANUAL_REVIEW, DISQUALIFIED)

# Company qualification tiers (Part 2).
ACTIVE_VACANCY = "active_vacancy"
HIGH_SIGNAL = "high_signal_speculative"
RESEARCH_CANDIDATE = "research_candidate"
UNQUALIFIED = "unqualified"

# Contact email verification status.
EMAIL_VERIFIED = "verified"
EMAIL_UNVERIFIED = "unverified"
EMAIL_UNAVAILABLE = "unavailable"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach_companies (
    company_id       TEXT PRIMARY KEY,   -- dedup_key from company.py
    name             TEXT NOT NULL,
    domain           TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL DEFAULT 'discovered',
    qualification    TEXT NOT NULL DEFAULT '',
    score            REAL NOT NULL DEFAULT 0,
    evidence         TEXT NOT NULL DEFAULT '[]',
    last_updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    full_name        TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    role_kind        TEXT NOT NULL DEFAULT '',  -- cto/em/recruiter/founder
    email            TEXT NOT NULL DEFAULT '',
    email_status     TEXT NOT NULL DEFAULT 'unavailable',
    linkedin_url     TEXT NOT NULL DEFAULT '',
    source_url       TEXT NOT NULL DEFAULT '',
    last_enriched    TEXT NOT NULL DEFAULT '',
    dedup_key        TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_enrichment (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id       INTEGER NOT NULL,
    source           TEXT NOT NULL,
    credits_used     INTEGER NOT NULL DEFAULT 0,
    enriched_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_evidence (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    kind             TEXT NOT NULL,      -- product_launch/eng_blog/oss/hiring/expansion
    fact             TEXT NOT NULL,
    url              TEXT NOT NULL DEFAULT '',
    observed_at      TEXT NOT NULL DEFAULT '',
    UNIQUE(company_id, kind, fact)
);
CREATE TABLE IF NOT EXISTS applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER NOT NULL,
    company_id       TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL DEFAULT 'draft',  -- draft/ready_for_review/approved/submitted
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_drafts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    contact_id       INTEGER,
    job_id           INTEGER,
    draft_type       TEXT NOT NULL,      -- vacancy/internship/referral/founder/followup
    subject          TEXT NOT NULL DEFAULT '',
    body             TEXT NOT NULL,
    evidence_map     TEXT NOT NULL DEFAULT '{}',
    state            TEXT NOT NULL DEFAULT 'draft_ready',  -- draft_ready/awaiting_approval/approved/sent/rejected
    created_at       TEXT NOT NULL,
    approved_at      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS outbound_messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         INTEGER,
    contact_id       INTEGER,
    company_id       TEXT NOT NULL,
    gmail_thread_id  TEXT NOT NULL DEFAULT '',
    message_id       TEXT NOT NULL DEFAULT '',
    subject          TEXT NOT NULL DEFAULT '',
    body             TEXT NOT NULL,
    sent_at          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'draft'  -- draft/sent/failed/bounced
);
CREATE TABLE IF NOT EXISTS gmail_threads (
    thread_id        TEXT PRIMARY KEY,
    contact_id       INTEGER,
    company_id       TEXT NOT NULL,
    last_message_at  TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active'  -- active/replied/bounced/optout/closed
);
CREATE TABLE IF NOT EXISTS followup_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    contact_id       INTEGER,
    followup_number  INTEGER NOT NULL,   -- 1 or 2
    due_on           TEXT NOT NULL,      -- ISO date
    reason           TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/sent/skipped/cancelled
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL,
    from_addr        TEXT NOT NULL DEFAULT '',
    received_at      TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'reply',   -- reply/rejection/optout/bounce
    body             TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS interviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    contact_id       INTEGER,
    scheduled_at     TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'phone',   -- phone/technical/onsite
    notes            TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity           TEXT NOT NULL,      -- company/contact/draft/followup
    entity_id        TEXT NOT NULL,
    from_state       TEXT NOT NULL DEFAULT '',
    to_state         TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    at               TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def is_business_day(d: date, holidays: set = ()) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def add_business_days(start: date, n: int, holidays: set = ()) -> date:
    """Return start + n business days (skipping weekends and given holidays)."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if is_business_day(d, holidays):
            added += 1
    return d


def followup_due_date(sent_on: date, followup_number: int, holidays: set = ()) -> date:
    """First follow-up 3-5 business days, final 5-7 business days after that."""
    if followup_number == 1:
        return add_business_days(sent_on, 4, holidays)
    if followup_number == 2:
        base = add_business_days(sent_on, 4, holidays)
        return add_business_days(base, 6, holidays)
    raise ValueError("only two follow-ups are supported")


class OutreachStorage:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- audit ---------------------------------------------------------------
    def audit(self, entity: str, entity_id, from_state: str, to_state: str, note: str = ""):
        self.conn.execute(
            "INSERT INTO audit_events (entity, entity_id, from_state, to_state, note, at) "
            "VALUES (?,?,?,?,?,?)",
            (entity, str(entity_id), from_state, to_state, note, _now()))

    # -- companies -----------------------------------------------------------
    def upsert_company(self, company_id: str, name: str, domain: str,
                       qualification: str, score: float, evidence: list) -> None:
        existing = self.conn.execute(
            "SELECT state FROM outreach_companies WHERE company_id=?", (company_id,)).fetchone()
        state = existing["state"] if existing else DISCOVERED
        self.conn.execute(
            "INSERT INTO outreach_companies (company_id,name,domain,state,qualification,"
            "score,evidence,last_updated) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(company_id) DO UPDATE SET name=excluded.name, domain=excluded.domain, "
            "qualification=excluded.qualification, score=excluded.score, "
            "evidence=excluded.evidence, last_updated=excluded.last_updated",
            (company_id, name, domain, state, qualification, score,
             json.dumps(evidence), _now()))
        self.conn.commit()

    def transition_company(self, company_id: str, to_state: str, note: str = "") -> bool:
        if to_state not in COMPANY_STATES:
            raise ValueError(f"unknown state {to_state!r}")
        row = self.conn.execute(
            "SELECT state FROM outreach_companies WHERE company_id=?", (company_id,)).fetchone()
        if row is None:
            return False
        from_state = row["state"]
        self.conn.execute(
            "UPDATE outreach_companies SET state=?, last_updated=? WHERE company_id=?",
            (to_state, _now(), company_id))
        self.audit("company", company_id, from_state, to_state, note)
        self.conn.commit()
        return True

    # -- contacts ------------------------------------------------------------
    def upsert_contact(self, company_id: str, full_name: str, title: str,
                       role_kind: str, email: str = "", email_status: str = EMAIL_UNAVAILABLE,
                       linkedin_url: str = "", source_url: str = "") -> int:
        import hashlib
        key = hashlib.sha256(f"{company_id}|{full_name.lower()}".encode()).hexdigest()
        cur = self.conn.execute(
            "INSERT INTO contacts (company_id,full_name,title,role_kind,email,email_status,"
            "linkedin_url,source_url,dedup_key) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(dedup_key) DO UPDATE SET title=excluded.title, role_kind=excluded.role_kind, "
            "email=excluded.email, email_status=excluded.email_status, "
            "linkedin_url=excluded.linkedin_url, source_url=excluded.source_url",
            (company_id, full_name, title, role_kind, email, email_status,
             linkedin_url, source_url, key))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM contacts WHERE dedup_key=?", (key,)).fetchone()
        return row["id"]

    def record_enrichment(self, contact_id: int, source: str, credits_used: int):
        self.conn.execute(
            "INSERT INTO contact_enrichment (contact_id, source, credits_used, enriched_at) "
            "VALUES (?,?,?,?)", (contact_id, source, credits_used, _now()))
        self.conn.execute(
            "UPDATE contacts SET last_enriched=? WHERE id=?", (_now(), contact_id))
        self.conn.commit()

    def list_contacts(self, company_id: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM contacts"
        params = []
        if company_id:
            sql += " WHERE company_id = ?"
            params.append(company_id)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # -- evidence ------------------------------------------------------------
    def add_evidence(self, company_id: str, kind: str, fact: str, url: str = ""):
        self.conn.execute(
            "INSERT OR IGNORE INTO company_evidence (company_id,kind,fact,url,observed_at) "
            "VALUES (?,?,?,?,?)", (company_id, kind, fact, url, _today()))
        self.conn.commit()

    def list_evidence(self, company_id: str) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM company_evidence WHERE company_id=? ORDER BY observed_at DESC",
            (company_id,)).fetchall()]

    # -- drafts --------------------------------------------------------------
    def create_draft(self, company_id: str, draft_type: str, subject: str, body: str,
                     evidence_map: dict, contact_id: Optional[int] = None,
                     job_id: Optional[int] = None, state: str = DRAFT_READY) -> int:
        cur = self.conn.execute(
            "INSERT INTO outreach_drafts (company_id,contact_id,job_id,draft_type,subject,"
            "body,evidence_map,state,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (company_id, contact_id, job_id, draft_type, subject, body,
             json.dumps(evidence_map), state, _now()))
        self.conn.commit()
        return cur.lastrowid

    def approve_draft(self, draft_id: int) -> bool:
        row = self.conn.execute(
            "SELECT state FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute(
            "UPDATE outreach_drafts SET state='approved', approved_at=? WHERE id=?",
            (_now(), draft_id))
        self.audit("draft", draft_id, row["state"], "approved", "")
        self.conn.commit()
        return True

    def list_drafts(self, state: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM outreach_drafts"
        params = []
        if state:
            sql += " WHERE state=?"
            params.append(state)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # -- threads / follow-ups ------------------------------------------------
    def upsert_thread(self, thread_id: str, company_id: str, contact_id: Optional[int],
                      status: str = "active"):
        self.conn.execute(
            "INSERT INTO gmail_threads (thread_id,contact_id,company_id,status) VALUES (?,?,?,?) "
            "ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status",
            (thread_id, contact_id, company_id, status))
        self.conn.commit()

    def record_reply(self, thread_id: str, kind: str, from_addr: str = "",
                     body: str = ""):
        self.conn.execute(
            "INSERT INTO replies (thread_id,from_addr,received_at,kind,body) VALUES (?,?,?,?,?)",
            (thread_id, from_addr, _now(), kind, body))
        # substantive reply or rejection/optout/bounce closes the thread.
        if kind in ("reply", "rejection", "optout", "bounce"):
            self.conn.execute(
                "UPDATE gmail_threads SET status=? WHERE thread_id=?",
                ("replied" if kind == "reply" else kind, thread_id))
        self.conn.commit()

    def has_reply_or_optout(self, thread_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM gmail_threads WHERE thread_id=?", (thread_id,)).fetchone()
        return bool(row and row["status"] in ("replied", "rejection", "optout", "bounce"))

    def create_followup(self, thread_id: str, company_id: str, contact_id: Optional[int],
                        number: int, due_on: str, reason: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO followup_tasks (thread_id,company_id,contact_id,followup_number,"
            "due_on,reason,state,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (thread_id, company_id, contact_id, number, due_on, reason, "pending", _now()))
        self.conn.commit()
        return cur.lastrowid

    def list_due_followups(self, today: Optional[str] = None, holidays: set = ()) -> List[dict]:
        """Return pending follow-ups due on/before `today`, suppressing any whose
        thread has since received a reply, rejection, optout or bounce."""
        today = today or _today()
        rows = self.conn.execute(
            "SELECT * FROM followup_tasks WHERE state='pending' AND due_on <= ? ORDER BY due_on",
            (today,)).fetchall()
        out = []
        for r in rows:
            if self.has_reply_or_optout(r["thread_id"]):
                self.conn.execute(
                    "UPDATE followup_tasks SET state='cancelled' WHERE id=?", (r["id"],))
                continue
            out.append(dict(r))
        self.conn.commit()
        return out

    # -- metrics -------------------------------------------------------------
    def metrics(self) -> dict:
        def count(sql, params=()):
            return int(self.conn.execute(sql, params).fetchone()["c"])
        return {
            "companies": count("SELECT COUNT(*) c FROM outreach_companies"),
            "qualified": count("SELECT COUNT(*) c FROM outreach_companies WHERE qualification != ''"),
            "contacts": count("SELECT COUNT(*) c FROM contacts"),
            "contacts_verified": count(
                "SELECT COUNT(*) c FROM contacts WHERE email_status='verified'"),
            "drafts": count("SELECT COUNT(*) c FROM outreach_drafts"),
            "drafts_awaiting": count(
                "SELECT COUNT(*) c FROM outreach_drafts WHERE state='awaiting_approval'"),
            "sent": count(
                "SELECT COUNT(*) c FROM outbound_messages WHERE status='sent'"),
            "replies": count("SELECT COUNT(*) c FROM replies WHERE kind='reply'"),
            "interviews": count("SELECT COUNT(*) c FROM interviews"),
            "followups_due": count(
                "SELECT COUNT(*) c FROM followup_tasks WHERE state='pending'"),
        }
