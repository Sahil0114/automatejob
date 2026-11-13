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
CREATE TABLE IF NOT EXISTS holidays (
    day              TEXT PRIMARY KEY,   -- ISO date, caller-approved holidays only
    note             TEXT NOT NULL DEFAULT ''
);
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
    dedup_key        TEXT UNIQUE NOT NULL,
    is_fixture       INTEGER NOT NULL DEFAULT 0,
    fixture_note     TEXT NOT NULL DEFAULT ''
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
    approved_at      TEXT NOT NULL DEFAULT '',
    gmail_draft_id   TEXT NOT NULL DEFAULT '',
    gmail_message_id TEXT NOT NULL DEFAULT '',
    gmail_thread_id  TEXT NOT NULL DEFAULT '',
    recipient        TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS recruiter_posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key        TEXT UNIQUE NOT NULL,
    original_url     TEXT NOT NULL,
    source_type      TEXT NOT NULL DEFAULT 'manual',
    raw_text         TEXT NOT NULL DEFAULT '',
    company          TEXT NOT NULL DEFAULT '',
    recruiter_name   TEXT NOT NULL DEFAULT '',
    recruiter_title  TEXT NOT NULL DEFAULT '',
    recruiter_linkedin_url TEXT NOT NULL DEFAULT '',
    published_at     TEXT NOT NULL DEFAULT '',
    collected_at     TEXT NOT NULL,
    last_verified_at TEXT NOT NULL DEFAULT '',
    confidence       REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'unverified',
    evidence         TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS recruiter_post_roles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          INTEGER NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    experience       TEXT NOT NULL DEFAULT '',
    technical_requirements TEXT NOT NULL DEFAULT '[]',
    salary           TEXT NOT NULL DEFAULT '',
    deadline         TEXT NOT NULL DEFAULT '',
    application_url  TEXT NOT NULL DEFAULT '',
    published_email  TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'unverified'
);
CREATE TABLE IF NOT EXISTS recruiter_post_emails (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          INTEGER NOT NULL,
    email            TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'published',
    UNIQUE(post_id, email)
);
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


_OUTREACH_MIGRATIONS = (
    ("contacts", "is_fixture", "INTEGER NOT NULL DEFAULT 0"),
    ("contacts", "fixture_note", "TEXT NOT NULL DEFAULT ''"),
    ("outreach_drafts", "gmail_draft_id", "TEXT NOT NULL DEFAULT ''"),
    ("outreach_drafts", "gmail_message_id", "TEXT NOT NULL DEFAULT ''"),
    ("outreach_drafts", "gmail_thread_id", "TEXT NOT NULL DEFAULT ''"),
    ("outreach_drafts", "recipient", "TEXT NOT NULL DEFAULT ''"),
    ("followup_tasks", "draft_id", "INTEGER"),
    ("followup_tasks", "sent_at", "TEXT NOT NULL DEFAULT ''"),
    ("followup_tasks", "approved_at", "TEXT NOT NULL DEFAULT ''"),
    ("followup_tasks", "skipped_reason", "TEXT NOT NULL DEFAULT ''"),
)


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
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, column, declaration in _OUTREACH_MIGRATIONS:
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        # Exactly two follow-ups per thread, enforced at the schema level.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_followups_thread_number "
            "ON followup_tasks(thread_id, followup_number)")

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

    def ensure_draft(self, company_id: str, draft_type: str, subject: str, body: str,
                     evidence_map: dict, contact_id: Optional[int] = None,
                     job_id: Optional[int] = None) -> tuple[int, bool]:
        """Create a local reviewable draft once; return (id, created).

        Repeating the same contact-save event must not produce another pending
        identical draft. Rejected/sent drafts do not block a later new draft.
        """
        if contact_id is not None:
            row = self.conn.execute(
                "SELECT id FROM outreach_drafts WHERE contact_id=? AND draft_type=? "
                "AND subject=? AND body=? AND state NOT IN ('rejected','sent') "
                "ORDER BY id DESC LIMIT 1",
                (contact_id, draft_type, subject, body)).fetchone()
            if row:
                return row["id"], False
        return self.create_draft(company_id, draft_type, subject, body, evidence_map,
                                 contact_id=contact_id, job_id=job_id), True

    def update_draft(self, draft_id: int, subject: str, body: str) -> bool:
        """Update a local draft and require fresh approval after any edit.

        Gmail-created and sent drafts are immutable locally to avoid divergence
        from the remote message. Editing an approved draft returns it to
        ``draft_ready`` rather than preserving stale approval.
        """
        row = self.conn.execute(
            "SELECT state FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None or row["state"] in ("gmail_draft", "sent"):
            return False
        new_state = "draft_ready"
        self.conn.execute(
            "UPDATE outreach_drafts SET subject=?, body=?, state=?, approved_at='' WHERE id=?",
            (subject, body, new_state, draft_id))
        self.audit("draft", draft_id, row["state"], new_state, "edited")
        self.conn.commit()
        return True

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

    def record_gmail_draft(self, draft_id: int, gmail_draft_id: str,
                           gmail_message_id: str, gmail_thread_id: str,
                           recipient: str) -> dict:
        """Persist Gmail draft metadata exactly once (safe on retry).

        A duplicate dashboard click must return the originally persisted Gmail
        id and must not overwrite it; callers check this before another Gmail
        API create call.
        """
        row = self.conn.execute(
            "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown draft {draft_id}")
        if row["gmail_draft_id"]:
            return dict(row)
        self.conn.execute(
            "UPDATE outreach_drafts SET state='gmail_draft', gmail_draft_id=?, "
            "gmail_message_id=?, gmail_thread_id=?, recipient=? WHERE id=?",
            (gmail_draft_id, gmail_message_id, gmail_thread_id, recipient, draft_id))
        self.audit("draft", draft_id, row["state"], "gmail_draft", "Gmail draft created")
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone())

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
            self._suppress_after_terminal_reply(thread_id, kind)
        self.conn.commit()

    def _suppress_after_terminal_reply(self, thread_id: str, kind: str) -> None:
        """Record a suppression after a terminal reply state.

        Opt-out suppresses the whole company. Rejection/bounce suppresses the
        contact (and company when no contact is linked). A plain reply only
        closes follow-ups; it does not block future contact.
        """
        if kind not in ("rejection", "optout", "bounce"):
            return
        row = self.conn.execute(
            "SELECT company_id, contact_id FROM gmail_threads WHERE thread_id=?",
            (thread_id,)).fetchone()
        if not row:
            return
        reason = f"auto-suppressed after {kind} on thread {thread_id}"
        if kind == "optout":
            self.suppress("company", row["company_id"], reason)
        elif row["contact_id"]:
            self.suppress("contact", str(row["contact_id"]), reason)
        else:
            self.suppress("company", row["company_id"], reason)

    def has_reply_or_optout(self, thread_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM gmail_threads WHERE thread_id=?", (thread_id,)).fetchone()
        return bool(row and row["status"] in ("replied", "rejection", "optout", "bounce"))

    def create_followup(self, thread_id: str, company_id: str, contact_id: Optional[int],
                        number: int, due_on: str, reason: str) -> int:
        """Create a follow-up task idempotently.

        At most two follow-ups per thread (also enforced by a unique index).
        If one already exists for the same thread/number, the existing id is
        returned and due_on/reason are left untouched.
        """
        if number not in (1, 2):
            raise ValueError("only two follow-ups are supported")
        existing = self.conn.execute(
            "SELECT id FROM followup_tasks WHERE thread_id=? AND followup_number=?",
            (thread_id, number)).fetchone()
        if existing:
            return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO followup_tasks (thread_id,company_id,contact_id,followup_number,"
            "due_on,reason,state,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (thread_id, company_id, contact_id, number, due_on, reason, "pending", _now()))
        self.conn.commit()
        return cur.lastrowid

    def holidays(self) -> set:
        """Approved holiday dates used for business-day scheduling."""
        return {r["day"] for r in self.conn.execute("SELECT day FROM holidays").fetchall()}

    def add_holiday(self, day: str, note: str = "") -> bool:
        """Add an explicit, caller-approved holiday (YYYY-MM-DD). Returns True if added."""
        day = (day or "").strip()
        try:
            date.fromisoformat(day)
        except ValueError:
            raise ValueError(f"invalid holiday date {day!r} (expected YYYY-MM-DD)")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO holidays (day, note) VALUES (?,?)", (day, note))
        self.conn.commit()
        return cur.rowcount > 0

    def remove_holiday(self, day: str) -> bool:
        cur = self.conn.execute("DELETE FROM holidays WHERE day=?", (day.strip(),))
        self.conn.commit()
        return cur.rowcount > 0

    def list_holidays(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT day, note FROM holidays ORDER BY day").fetchall()]

    def schedule_followups_for_thread(self, thread_id: str, company_id: str,
                                      contact_id: Optional[int],
                                      sent_on: Optional[date] = None,
                                      reason: str = "no reply to outbound message") -> list:
        """Create follow-up #1 (4 business days) and #2 (6 further business days).

        Idempotent: existing tasks are left alone. Never runs for unsent work —
        callers only invoke this for recorded sends/manual sends.
        """
        sent_on = sent_on or date.today()
        holidays = self.holidays()
        created = []
        for number in (1, 2):
            due = followup_due_date(sent_on, number, holidays).isoformat()
            fid = self.create_followup(thread_id, company_id, contact_id, number, due, reason)
            created.append(fid)
        return created

    def list_followups(self, state: Optional[str] = None) -> List[dict]:
        sql = ("SELECT f.*, c.full_name AS contact_name, c.email AS contact_email, "
               "o.name AS company_name "
               "FROM followup_tasks f "
               "LEFT JOIN contacts c ON c.id = f.contact_id "
               "LEFT JOIN outreach_companies o ON o.company_id = f.company_id")
        params = []
        if state:
            sql += " WHERE f.state=?"
            params.append(state)
        sql += " ORDER BY f.due_on, f.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_followup(self, followup_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM followup_tasks WHERE id=?",
                                (followup_id,)).fetchone()
        return dict(row) if row else None

    def approve_followup(self, followup_id: int) -> bool:
        row = self.conn.execute("SELECT state FROM followup_tasks WHERE id=?",
                                (followup_id,)).fetchone()
        if row is None:
            return False
        if row["state"] != "pending":
            return False
        self.conn.execute(
            "UPDATE followup_tasks SET state='approved', approved_at=? WHERE id=?",
            (_now(), followup_id))
        self.audit("followup", followup_id, "pending", "approved", "")
        self.conn.commit()
        return True

    def skip_followup(self, followup_id: int, reason: str = "") -> bool:
        row = self.conn.execute("SELECT state FROM followup_tasks WHERE id=?",
                                (followup_id,)).fetchone()
        if row is None:
            return False
        if row["state"] not in ("pending", "approved"):
            return False
        self.conn.execute(
            "UPDATE followup_tasks SET state='skipped', skipped_reason=? WHERE id=?",
            (reason, followup_id))
        self.audit("followup", followup_id, row["state"], "skipped", reason)
        self.conn.commit()
        return True

    def mark_followup_sent(self, followup_id: int, sent_at: Optional[str] = None) -> bool:
        """Mark an APPROVED follow-up as sent. Never triggers a real send."""
        row = self.conn.execute("SELECT state FROM followup_tasks WHERE id=?",
                                (followup_id,)).fetchone()
        if row is None:
            return False
        if row["state"] == "sent":
            return True
        if row["state"] != "approved":
            raise ValueError("follow-up must be approved before marking as sent")
        self.conn.execute(
            "UPDATE followup_tasks SET state='sent', sent_at=? WHERE id=?",
            (sent_at or _now(), followup_id))
        self.audit("followup", followup_id, "approved", "sent", "")
        self.conn.commit()
        return True

    def attach_followup_draft(self, followup_id: int, draft_id: int) -> bool:
        row = self.conn.execute("SELECT id FROM followup_tasks WHERE id=?",
                                (followup_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute("UPDATE followup_tasks SET draft_id=? WHERE id=?",
                          (draft_id, followup_id))
        self.conn.commit()
        return True

    def list_due_followups(self, today: Optional[str] = None, holidays: Optional[set] = None) -> List[dict]:
        """Return pending follow-ups due on/before `today`, suppressing any whose
        thread has since received a reply, rejection, optout or bounce.

        `holidays` defaults to the stored holiday calendar used by the
        scheduler; pass an explicit set to override."""
        today = today or _today()
        if holidays is None:
            holidays = self.holidays()
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
    def get_contact(self, contact_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return dict(row) if row else None

    def update_contact(self, contact_id: int, *, full_name: Optional[str] = None,
                       title: Optional[str] = None, role_kind: Optional[str] = None,
                       email: Optional[str] = None, email_status: Optional[str] = None,
                       linkedin_url: Optional[str] = None,
                       source_url: Optional[str] = None) -> bool:
        row = self.conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if row is None:
            return False
        values = {
            "full_name": row["full_name"] if full_name is None else full_name,
            "title": row["title"] if title is None else title,
            "role_kind": row["role_kind"] if role_kind is None else role_kind,
            "email": row["email"] if email is None else (email or "").strip(),
            "email_status": row["email_status"] if email_status is None else email_status,
            "linkedin_url": row["linkedin_url"] if linkedin_url is None else linkedin_url,
            "source_url": row["source_url"] if source_url is None else source_url,
        }
        if values["email"] and values["email_status"] not in ("verified", "unverified", "unavailable"):
            values["email_status"] = "unverified"
        self.conn.execute(
            "UPDATE contacts SET full_name=?, title=?, role_kind=?, email=?, email_status=?, "
            "linkedin_url=?, source_url=? WHERE id=?",
            (values["full_name"], values["title"], values["role_kind"],
             values["email"], values["email_status"], values["linkedin_url"],
             values["source_url"], contact_id))
        self.conn.commit()
        return True

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

    def list_contacts_joined(self, include_fixtures: bool = False) -> List[dict]:
        sql = (
            "SELECT c.*, o.name AS company_name, o.domain AS company_domain, o.state AS company_state "
            "FROM contacts c LEFT JOIN outreach_companies o ON c.company_id=o.company_id"
        )
        if not include_fixtures:
            sql += " WHERE c.is_fixture=0"
        sql += " ORDER BY c.id DESC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def mark_contact_fixture(self, contact_id: int, note: str) -> bool:
        """Hide an accidental fixture/smoke-test contact without deleting it."""
        cur = self.conn.execute(
            "UPDATE contacts SET is_fixture=1, fixture_note=? WHERE id=?", (note, contact_id))
        if cur.rowcount:
            self.audit("contact", contact_id, "production", "fixture", note)
        self.conn.commit()
        return cur.rowcount > 0

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
            if status == MSG_SENT:
                # Only real sends schedule follow-ups; drafts never do.
                self.schedule_followups_for_thread(
                    gmail_thread_id, company_id, contact_id, date.today())
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
        returns the existing message id. Never triggers a real send.

        If no thread_id is supplied, the draft's persisted gmail_thread_id is
        used (when present) so follow-ups stay attached to the same thread.
        Follow-ups are scheduled from the actual sent date when available.
        """
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
        if not thread_id:
            thread_id = row["gmail_thread_id"] or f"manual-draft-{draft_id}"
        msg_id = self.record_send(
            draft_id, row["contact_id"], row["company_id"], row["subject"],
            row["body"], gmail_thread_id=thread_id, status=MSG_SENT)
        # record_send stamps _now(); overwrite with the manual timestamp.
        self.conn.execute(
            "UPDATE outbound_messages SET sent_at=?, status='sent', gmail_thread_id=? WHERE id=?",
            (sent_at, thread_id, msg_id))
        self.conn.execute(
            "UPDATE outreach_drafts SET state='sent', gmail_thread_id=? WHERE id=?",
            (thread_id, draft_id))
        # Idempotent: record_send already schedules for new inserts; ensure
        # existing manual marks also get follow-ups for this thread.
        sent_date = None
        try:
            sent_date = date.fromisoformat(str(sent_at)[:10])
        except ValueError:
            sent_date = date.today()
        self.schedule_followups_for_thread(
            thread_id, row["company_id"], row["contact_id"], sent_date)
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

    def update_application(self, application_id: int, status: Optional[str] = None,
                           notes: Optional[str] = None) -> bool:
        """Update the status/notes of an existing application. Never deletes."""
        row = self.conn.execute("SELECT * FROM job_applications WHERE id=?",
                                (application_id,)).fetchone()
        if row is None:
            return False
        allowed = {"applied", "in_review", "interview", "offer", "rejected", "withdrawn"}
        new_status = status if status is not None else row["status"]
        new_notes = notes if notes is not None else row["notes"]
        if new_status not in allowed:
            raise ValueError(f"invalid application status {new_status!r}")
        self.conn.execute(
            "UPDATE job_applications SET status=?, notes=? WHERE id=?",
            (new_status, new_notes, application_id))
        self.audit("application", application_id, row["status"], new_status, "updated")
        self.conn.commit()
        return True

    # -- recruiter posts -----------------------------------------------------
    def upsert_recruiter_post(self, post) -> int:
        """Insert/update a recruiter post idempotently by original URL.

        Roles and published emails are replaced on update; nothing is deleted
        from history. Unknown fields remain empty rather than inferred.
        """
        import hashlib
        import json as _json
        dedup = hashlib.sha256((post.original_url or post.text[:200]).encode()).hexdigest()
        existing = self.conn.execute(
            "SELECT id FROM recruiter_posts WHERE dedup_key=?", (dedup,)).fetchone()
        now = _now()
        if existing:
            post_id = existing["id"]
            self.conn.execute(
                "UPDATE recruiter_posts SET raw_text=?, company=?, recruiter_name=?, "
                "recruiter_title=?, recruiter_linkedin_url=?, published_at=?, source_type=?, "
                "confidence=?, last_verified_at=? WHERE id=?",
                (post.text, post.company, post.recruiter_name, post.recruiter_title,
                 post.recruiter_linkedin_url, post.published_at, post.source_type,
                 post.confidence, now, post_id))
            self.conn.execute("DELETE FROM recruiter_post_roles WHERE post_id=?", (post_id,))
            self.conn.execute("DELETE FROM recruiter_post_emails WHERE post_id=?", (post_id,))
        else:
            cur = self.conn.execute(
                "INSERT INTO recruiter_posts (dedup_key,original_url,source_type,raw_text,company,"
                "recruiter_name,recruiter_title,recruiter_linkedin_url,published_at,collected_at,"
                "last_verified_at,confidence,status,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (dedup, post.original_url, post.source_type, post.text, post.company,
                 post.recruiter_name, post.recruiter_title, post.recruiter_linkedin_url,
                 post.published_at, now, now, post.confidence, "unverified",
                 _json.dumps({"source_url": post.original_url})))
            post_id = int(cur.lastrowid or 0)
        for role in post.roles:
            self.conn.execute(
                "INSERT INTO recruiter_post_roles (post_id,title,location,experience,"
                "technical_requirements,salary,deadline,application_url,published_email,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (post_id, role.title, role.location, role.experience,
                 _json.dumps(role.technical_requirements), role.salary, role.deadline,
                 role.application_url, role.published_email, "unverified"))
        for email in post.published_emails:
            self.conn.execute(
                "INSERT OR IGNORE INTO recruiter_post_emails (post_id,email,verification_status) "
                "VALUES (?,?,?)", (post_id, email, "published"))
        self.conn.commit()
        return post_id

    def set_recruiter_post_status(self, post_id: int, status: str, note: str = "") -> bool:
        if status not in ("unverified", "verified", "irrelevant", "expired", "applied"):
            raise ValueError(f"invalid recruiter post status {status!r}")
        cur = self.conn.execute(
            "UPDATE recruiter_posts SET status=? WHERE id=?", (status, post_id))
        self.conn.commit()
        return cur.rowcount > 0

    def list_recruiter_posts(self, status: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM recruiter_posts"
        params = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY collected_at DESC"
        out = []
        for row in self.conn.execute(sql, params).fetchall():
            d = dict(row)
            d["roles"] = [dict(r) for r in self.conn.execute(
                "SELECT * FROM recruiter_post_roles WHERE post_id=? ORDER BY id", (d["id"],)).fetchall()]
            d["published_emails"] = [r["email"] for r in self.conn.execute(
                "SELECT email FROM recruiter_post_emails WHERE post_id=? ORDER BY id", (d["id"],)).fetchall()]
            out.append(d)
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
