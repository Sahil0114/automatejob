"""Deterministic recruiter-post normalization.

This module deliberately extracts only explicit data from public/manual post
text. It does not infer a recruiter, hiring intent, publication date, company,
or email ownership. Unknown fields stay blank and can be reviewed in the
recruiter-post dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_ROLE_WORDS = re.compile(
    r"\b(?:engineer|developer|devops|sre|platform|backend|software|intern|internship|qa|cloud)\b", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*(.+?)\s*$")


@dataclass
class RecruiterRole:
    title: str
    location: str = ""
    experience: str = ""
    technical_requirements: List[str] = field(default_factory=list)
    salary: str = ""
    deadline: str = ""
    application_url: str = ""
    published_email: str = ""


@dataclass
class RecruiterPost:
    original_url: str
    text: str = ""
    company: str = ""
    recruiter_name: str = ""
    recruiter_title: str = ""
    recruiter_linkedin_url: str = ""
    published_at: str = ""
    source_type: str = "manual"
    confidence: float = 0.0
    roles: List[RecruiterRole] = field(default_factory=list)
    published_emails: List[str] = field(default_factory=list)


def _valid_email(email: str) -> bool:
    local, _, domain = email.partition("@")
    return bool(local and domain and "." in domain and not domain.startswith("."))


def parse_recruiter_post(text: str, *, original_url: str, company: str = "",
                         published_at: str = "", source_type: str = "manual") -> RecruiterPost:
    """Extract explicit bulleted technical roles and syntactically valid emails.

    This intentionally avoids guessing company/recruiter/date from prose. A
    caller supplies those only if the original post explicitly supports them.
    """
    roles = []
    for line in (text or "").splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        title = match.group(1).strip()
        # Drop parenthetical requirements after the role title but retain only
        # a clear role-bearing bullet.
        title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
        if _ROLE_WORDS.search(title):
            roles.append(RecruiterRole(title=title))
    emails = []
    for email in _EMAIL_RE.findall(text or ""):
        email = email.lower()
        if _valid_email(email) and email not in emails:
            emails.append(email)
    return RecruiterPost(
        original_url=original_url,
        text=text or "",
        company=company,
        published_at=published_at,
        source_type=source_type,
        confidence=0.6 if roles or emails else 0.0,
        roles=roles,
        published_emails=emails,
    )
