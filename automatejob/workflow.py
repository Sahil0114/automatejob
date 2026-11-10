"""Manual-contact workflow: the primary contact-discovery path.

No Apollo API is required. Contacts can be added manually (name/title/role/
email pasted from Apollo or elsewhere) or imported from CSV. On saving a
contact, the system validates the email, checks duplicates and suppression
lists, then generates a personalized draft tailored to the person's role.

Sending is strictly gated: a draft is created locally; creating it as a Gmail
draft and actually sending are separate explicit actions. Entering an email
never triggers sending. Nothing is sent without per-message approval.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .email_draft import draft_email, draft_for_contact
from .gmail import GmailAdapter
from .outreach import (
    MSG_CONFIRMATION_UNKNOWN, MSG_GMAIL_DRAFT, MSG_SENT, OutreachStorage,
)
from .profile import CandidateProfile, UnverifiedField

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


@dataclass
class ContactResult:
    contact_id: int
    draft: dict
    warnings: List[str] = field(default_factory=list)


def add_contact_and_draft(outreach: OutreachStorage, profile: CandidateProfile,
                          company_id: str, full_name: str, title: str,
                          role_kind: str, email: str = "",
                          email_status: str = "unverified",
                          linkedin_url: str = "", source_url: str = "") -> ContactResult:
    """Add/update a contact and generate a draft. Never sends anything."""
    warnings: List[str] = []
    email = (email or "").strip()

    # resolve canonical company id from id/domain/name
    company = outreach.get_company(company_id)
    if company is not None:
        company_id = company["company_id"]
    else:
        warnings.append(f"company {company_id!r} not in outreach DB — "
                        "draft uses the literal identifier")

    # 1. validate email format (if provided)
    if email and not validate_email(email):
        warnings.append(f"email {email!r} failed format validation")

    # 2. duplicate email across any company
    if email:
        existing = outreach.contact_by_email(email)
        if existing:
            warnings.append(f"email already belongs to contact #{existing['id']} "
                            f"({existing['full_name']})")

        # A corporate address with a different domain is not proof of an
        # employer relationship. Keep the contact/draft editable, but flag it
        # for manual review rather than silently treating it as confirmed.
        company_domain = (company or {}).get("domain", "").lower().removeprefix("www.")
        email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        personal_domains = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me", "protonmail.com"}
        if (company_domain and email_domain and email_domain not in personal_domains
                and email_domain != company_domain
                and not email_domain.endswith("." + company_domain)):
            warnings.append("email domain does not match intended company domain — "
                            "verify the contact-company association")

    # 3. suppression checks
    if outreach.is_suppressed(company_id=company_id, email=email):
        warnings.append("company or email is suppressed")

    # manually-entered emails are unverified unless verification evidence given
    if email and email_status not in ("verified",):
        email_status = "unverified"
        warnings.append("email marked unverified (no verification evidence supplied)")

    contact_id = outreach.upsert_contact(
        company_id, full_name, title, role_kind, email=email,
        email_status=email_status if email else "unavailable",
        linkedin_url=linkedin_url, source_url=source_url)

    # 4. generate personalized draft (fails honestly on unverified profile facts)
    company = outreach.get_company(company_id) or {"name": company_id, "domain": ""}
    evidence = outreach.list_evidence(company_id)

    # If the user already applied to this company, draft a follow-up rather
    # than a cold application.
    existing_app = outreach.latest_application(company_id)
    try:
        if existing_app is not None:
            draft = draft_email(
                profile, company,
                {"first_name": (full_name or "").split()[0] if full_name else "",
                 "role_kind": role_kind},
                "followup", evidence=evidence)
            draft["evidence_map"]["existing_application"] = (
                f"{existing_app['role']} applied {existing_app['applied_at']}")
            warnings.append(f"existing application to {company.get('name', company_id)} "
                            f"({existing_app['role']}) — drafted a follow-up, not a new application")
        else:
            draft = draft_for_contact(
                profile, company,
                {"first_name": (full_name or "").split()[0] if full_name else "",
                 "role_kind": role_kind},
                evidence=evidence)
    except UnverifiedField as e:
        draft = {"subject": "", "body": "",
                 "evidence_map": {}, "error": f"unverified profile field: {e}"}
        warnings.append(draft["error"])

    return ContactResult(contact_id, draft, warnings)


def import_contacts_csv(outreach: OutreachStorage, profile: CandidateProfile,
                        path: str) -> dict:
    """Import contacts from CSV. Columns: company_id,name,title,role_kind,
    email,email_status,linkedin_url,source_url (email_status optional)."""
    imported, skipped, errors = 0, 0, []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            email = (row.get("email") or "").strip()
            if not email or not validate_email(email):
                skipped += 1
                errors.append(f"row {i}: invalid/empty email")
                continue
            company_id = (row.get("company_id") or "").strip()
            name = (row.get("name") or "").strip()
            if not company_id or not name:
                skipped += 1
                errors.append(f"row {i}: missing company_id or name")
                continue
            add_contact_and_draft(
                outreach, profile, company_id, name,
                (row.get("title") or "").strip(), (row.get("role_kind") or "").strip(),
                email, email_status=(row.get("email_status") or "unverified").strip(),
                linkedin_url=(row.get("linkedin_url") or "").strip(),
                source_url=(row.get("source_url") or "").strip())
            imported += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}


def send_draft(outreach: OutreachStorage, gmail: GmailAdapter, draft_id: int,
               to_addr: str, sending_enabled: bool = False) -> dict:
    """Send an APPROVED draft via Gmail. Hard-gated: requires sending_enabled
    AND an authorized Gmail adapter AND the draft state to be 'approved'.
    Returns a status dict; never sends unless all guards pass."""
    row = outreach.conn.execute(
        "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        return {"ok": False, "status": "not_found"}
    if not sending_enabled:
        return {"ok": False, "status": "sending_disabled",
                "detail": "outbound sending is disabled by default"}
    if row["state"] != "approved":
        return {"ok": False, "status": "not_approved",
                "detail": "draft must be explicitly approved first"}
    if not gmail.available():
        return {"ok": False, "status": "gmail_unavailable",
                "detail": "Gmail OAuth not authorized"}

    result = gmail.send(to_addr, row["subject"], row["body"])
    # result: {status: sent|confirmation_unknown|failed, thread_id, message_id}
    if result.get("status") == "sent":
        msg_id = outreach.record_send(
            draft_id, row["contact_id"], row["company_id"], row["subject"],
            row["body"], gmail_thread_id=result.get("thread_id", ""),
            gmail_message_id=result.get("message_id", ""), status=MSG_SENT)
        outreach.conn.execute(
            "UPDATE outreach_drafts SET state='sent' WHERE id=?", (draft_id,))
        outreach.conn.commit()
        return {"ok": True, "status": "sent", "message_id": msg_id}
    if result.get("status") == "confirmation_unknown":
        msg_id = outreach.record_send(
            draft_id, row["contact_id"], row["company_id"], row["subject"],
            row["body"], status=MSG_CONFIRMATION_UNKNOWN)
        return {"ok": False, "status": "confirmation_unknown",
                "message_id": msg_id,
                "detail": "reconcile against Gmail before retrying"}
    return {"ok": False, "status": "failed", "detail": result.get("error", "")}


def sync_gmail_replies(outreach: OutreachStorage, gmail: GmailAdapter) -> dict:
    """Read authorized Gmail thread state and suppress follow-ups on replies.

    This is explicit/manual (never a scheduled sender). An unknown Gmail state
    is reported and left pending for review; it is never treated as "no reply".
    """
    if not gmail.available():
        return {"replies_recorded": 0, "unknown_threads": 0, "error": "gmail_unavailable"}
    replies_recorded = 0
    unknown_threads = 0
    threads = outreach.conn.execute(
        "SELECT thread_id FROM gmail_threads WHERE status='active'").fetchall()
    for row in threads:
        thread_id = row["thread_id"]
        has_reply = gmail.thread_has_reply(thread_id)
        if has_reply is True:
            outreach.record_reply(thread_id, "reply", from_addr="", body="")
            replies_recorded += 1
        elif has_reply is None:
            unknown_threads += 1
    return {"replies_recorded": replies_recorded, "unknown_threads": unknown_threads,
            "error": ""}
