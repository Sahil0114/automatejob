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
CREATE TABLE IF NOT EXISTS suppression (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scope            TEXT NOT NULL,      -- company / email / contact
    key              TEXT NOT NULL,      -- company_id or normalized email or contact_id
    reason           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    UNIQUE(scope, key)
);
CREATE TABLE IF NOT EXISTS linkedin_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    contact_id       INTEGER,
    profile_url      TEXT NOT NULL DEFAULT '',
    connection_note  TEXT NOT NULL DEFAULT '',
    post_accept_msg  TEXT NOT NULL DEFAULT '',
    referral_request TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending/in_progress/invited/connected/done/skipped
    last_action      TEXT NOT NULL DEFAULT '',
    next_action      TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_confirmations (
    field            TEXT PRIMARY KEY,
    value            TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT '',
    confirmed_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    company_name     TEXT NOT NULL DEFAULT '',
    role             TEXT NOT NULL DEFAULT '',
    job_url          TEXT NOT NULL DEFAULT '',
    applied_at       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'applied',  -- applied/in_review/interview/offer/rejected/withdrawn
    notes            TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'manual',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_company ON job_applications(company_id);
"""

# Outbound message states (kept distinct from Gmail draft states).
MSG_DRAFT = "draft"
MSG_GMAIL_DRAFT = "gmail_draft"
MSG_APPROVED = "approved"
MSG_SENT = "sent"
MSG_REPLIED = "replied"
MSG_BOUNCED = "bounced"
MSG_REJECTED = "rejected"
MSG_OPTED_OUT = "opted_out"
MSG_CONFIRMATION_UNKNOWN = "confirmation_unknown"
MSG_FAILED = "failed"


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

    # -- suppression ---------------------------------------------------------
    def suppress(self, scope: str, key: str, reason: str = "") -> bool:
        """Scope is 'company' (company_id), 'email' (normalized email) or
        'contact' (contact_id). Returns True if newly added."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO suppression (scope,key,reason,created_at) VALUES (?,?,?,?)",
            (scope, key, reason, _now()))
        self.conn.commit()
        return cur.rowcount > 0

    def unsuppress(self, scope: str, key: str) -> bool:
        cur = self.conn.execute("DELETE FROM suppression WHERE scope=? AND key=?",
                                (scope, key))
        self.conn.commit()
        return cur.rowcount > 0

    def is_suppressed(self, company_id: Optional[str] = None,
                      email: Optional[str] = None, contact_id: Optional[int] = None) -> bool:
        if company_id and self.conn.execute(
                "SELECT 1 FROM suppression WHERE scope='company' AND key=?", (company_id,)).fetchone():
            return True
        if email and self.conn.execute(
                "SELECT 1 FROM suppression WHERE scope='email' AND key=?",
                (email.strip().lower(),)).fetchone():
            return True
        if contact_id is not None and self.conn.execute(
                "SELECT 1 FROM suppression WHERE scope='contact' AND key=?",
                (str(contact_id),)).fetchone():
            return True
        return False

    # -- contacts (email-aware) ---------------------------------------------
    def contact_by_email(self, email: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE lower(email)=?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def get_company(self, company_id: str) -> Optional[dict]:
        """Resolve a company by internal id, canonical domain, or name."""
        row = self.conn.execute(
            "SELECT * FROM outreach_companies WHERE company_id=?", (company_id,)).fetchone()
        if row:
            return dict(row)
        row = self.conn.execute(
            "SELECT * FROM outreach_companies WHERE domain=? OR name=? LIMIT 1",
            (company_id.strip().lower(), company_id)).fetchone()
        return dict(row) if row else None

    def list_contacts_joined(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT c.*, o.name AS company_name, o.domain AS company_domain, o.state AS company_state "
            "FROM contacts c LEFT JOIN outreach_companies o ON c.company_id=o.company_id "
            "ORDER BY c.id DESC").fetchall()]

    # -- outbound messages ---------------------------------------------------
    def record_send(self, draft_id: Optional[int], contact_id: Optional[int],
                    company_id: str, subject: str, body: str,
                    gmail_thread_id: str = "", gmail_message_id: str = "",
                    status: str = MSG_SENT) -> int:
        """Record an outbound message with durable idempotency key.

        If a message with the same draft_id already exists, returns the existing
        row id without inserting (idempotent). """
        if draft_id is not None:
            existing = self.conn.execute(
                "SELECT id FROM outbound_messages WHERE draft_id=?", (draft_id,)).fetchone()
            if existing:
                return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO outbound_messages (draft_id,contact_id,company_id,gmail_thread_id,"
            "message_id,subject,body,sent_at,status) VALUES (?,?,?,?,?,?,?,?,?)",
            (draft_id, contact_id, company_id, gmail_thread_id, gmail_message_id,
             subject, body, _now() if status == MSG_SENT else "", status))
        self.conn.commit()
        if gmail_thread_id:
            self.upsert_thread(gmail_thread_id, company_id, contact_id, status="active")
        return cur.lastrowid

    def mark_message(self, message_id: int, status: str, thread_id: str = "") -> bool:
        self.conn.execute(
            "UPDATE outbound_messages SET status=? WHERE id=?", (status, message_id))
        if thread_id:
            self.conn.execute(
                "UPDATE outbound_messages SET gmail_thread_id=? WHERE id=?",
                (thread_id, message_id))
        self.conn.commit()
        return True

    def sent_to(self, contact_id: int) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM outbound_messages WHERE contact_id=? ORDER BY id DESC",
            (contact_id,)).fetchall()]

    def mark_sent_manually(self, draft_id: int, sent_at: str, thread_id: str = "") -> int:
        """Mark an APPROVED draft as sent manually (copy-email workflow), recording
        the actual send timestamp. Idempotent: re-marking an already-sent draft
        returns the existing message id. Never triggers a real send."""
        row = self.conn.execute(
            "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError(f"draft {draft_id} not found")
        if row["state"] == "sent":
            existing = self.conn.execute(
                "SELECT id FROM outbound_messages WHERE draft_id=?", (draft_id,)).fetchone()
            if existing:
                return existing["id"]
        if row["state"] != "approved":
            raise ValueError("draft must be approved before marking as sent")
        msg_id = self.record_send(
            draft_id, row["contact_id"], row["company_id"], row["subject"],
            row["body"], gmail_thread_id=thread_id, status=MSG_SENT)
        # record_send stamps _now(); overwrite with the manual timestamp.
        self.conn.execute(
            "UPDATE outbound_messages SET sent_at=?, status='sent' WHERE id=?",
            (sent_at, msg_id))
        self.conn.execute(
            "UPDATE outreach_drafts SET state='sent' WHERE id=?", (draft_id,))
        self.conn.commit()
        return msg_id

    # -- linkedin queue ------------------------------------------------------
    def add_linkedin_task(self, company_id: str, contact_id: Optional[int],
                          profile_url: str, connection_note: str = "",
                          post_accept_msg: str = "", referral_request: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO linkedin_tasks (company_id,contact_id,profile_url,connection_note,"
            "post_accept_msg,referral_request,status,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (company_id, contact_id, profile_url, connection_note, post_accept_msg,
             referral_request, "pending", _now()))
        self.conn.commit()
        return cur.lastrowid

    def set_linkedin_status(self, task_id: int, status: str, note: str = "") -> bool:
        self.conn.execute(
            "UPDATE linkedin_tasks SET status=?, last_action=?, updated_at=? WHERE id=?",
            (status, note, _now(), task_id))
        self.conn.commit()
        return True

    def list_linkedin_tasks(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM linkedin_tasks ORDER BY id DESC").fetchall()]

    # -- profile confirmations ----------------------------------------------
    def confirm_profile(self, field: str, value: str, source: str = "user") -> bool:
        self.conn.execute(
            "INSERT INTO profile_confirmations (field,value,source,confirmed_at) VALUES (?,?,?,?) "
            "ON CONFLICT(field) DO UPDATE SET value=excluded.value, source=excluded.source, "
            "confirmed_at=excluded.confirmed_at", (field, value, source, _now()))
        self.conn.commit()
        return True

    def list_profile_confirmations(self) -> dict:
        return {r["field"]: r["value"] for r in self.conn.execute(
            "SELECT field, value FROM profile_confirmations").fetchall()}

    # -- applications --------------------------------------------------------
    def record_application(self, company_id: str, role: str, applied_at: str,
                           company_name: str = "", job_url: str = "",
                           status: str = "applied", notes: str = "",
                           source: str = "manual") -> int:
        """Record a job application. Deduplicates on company+role+applied_at."""
        existing = self.conn.execute(
            "SELECT id FROM job_applications WHERE company_id=? AND role=? AND applied_at=?",
            (company_id, role, applied_at)).fetchone()
        if existing:
            return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO job_applications (company_id,company_name,role,job_url,applied_at,"
            "status,notes,source,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (company_id, company_name, role, job_url, applied_at, status, notes, source, _now()))
        self.conn.commit()
        return cur.lastrowid

    def has_application(self, company_id: str) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM job_applications WHERE company_id=?", (company_id,)).fetchone())

    def latest_application(self, company_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM job_applications WHERE company_id=? ORDER BY applied_at DESC LIMIT 1",
            (company_id,)).fetchone()
        return dict(row) if row else None

    def list_applications(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM job_applications ORDER BY applied_at DESC").fetchall()]

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
