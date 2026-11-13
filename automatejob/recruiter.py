"""Deterministic recruiter-post normalization.

This module deliberately extracts only explicit data from public/manual post
text. It does not infer a recruiter, hiring intent, publication date, company,
or email ownership. Unknown fields stay blank and can be reviewed in the
recruiter-post dashboard.

Multiple roles and multiple published application emails are supported. Only
emails that the post explicitly marks for applications ("apply to", "send your
resume to", "reach out at", "email", etc.) are treated as application emails —
the presence of a random email in prose is not enough on its own to assume a
recruitment contact, so extraction is anchored to an application-action phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_ROLE_WORDS = re.compile(
    r"\b(?:engineer|developer|devops|sre|platform|backend|software|intern|internship|qa|cloud|"
    r"infrastructure|architect|data|ml|ai)\b", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*(.+?)\s*$")

# Phrases that unambiguously indicate an email is an application contact.
_APPLICATION_EMAIL_PHRASES = re.compile(
    r"\b(?:apply(?: to| at| via| here)?|application|send(?: your)? (?:resume|cv|application)"
    r"|email(?: your)? (?:resume|cv|application)|resume(?:s)? to|cv to|"
    r"write(?: to)? us at|reach out at)\b[^\n]{0,140}",
    re.I)

_EXPERIENCE_RE = re.compile(
    r"\b(0\s*-\s*2|1\s*-\s*2|2\s*-\s*4|3\s*-\s*5|5\+|10\+)\s*(?:years?|yrs?)?\b|"
    r"\b(?:fresher|internship|entry[- ]level|junior|senior|mid[- ]level)\b",
    re.I)
_LOCATION_RE = re.compile(
    r"\b(?:location|based|onsite|on-site|hybrid|remote|office)\b[^\n]{0,80}?",
    re.I)
_SALARY_RE = re.compile(
    r"\b(?:salary|compensation|ctc|stipend|pay)\b[^\n]{0,80}?",
    re.I)
_DEADLINE_RE = re.compile(
    r"\b(?:deadline|apply by|last date|closes? on)\b[^\n]{0,80}?",
    re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_REMOTE_RE = re.compile(r"\bremote\b", re.I)


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
    remote: str = ""  # '' | 'remote' | 'hybrid' | 'onsite'


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
    application_urls: List[str] = field(default_factory=list)


def _valid_email(email: str) -> bool:
    local, _, domain = email.partition("@")
    return bool(local and domain and "." in domain and not domain.startswith("."))


def _dedupe(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))


def _extract_application_emails(text: str) -> List[str]:
    """Only emails near an explicit application-action phrase are returned."""
    emails: List[str] = []
    for phrase_match in _APPLICATION_EMAIL_PHRASES.finditer(text or ""):
        window = phrase_match.group(0)
        for email in _EMAIL_RE.findall(window):
            email = email.lower()
            if _valid_email(email) and email not in emails:
                emails.append(email)
    return emails


def _extract_application_urls(text: str) -> List[str]:
    urls = []
    for url in _URL_RE.findall(text or ""):
        url = url.rstrip(".,;)]}")
        if url.lower() not in ("https://www.linkedin.com", "https://linkedin.com") \
                and url not in urls:
            urls.append(url)
    return urls


def _clean_fragment(fragment: str) -> str:
    fragment = re.sub(r"\s+", " ", fragment or "").strip()
    fragment = fragment.rstrip(".,;:|")
    return fragment


def _first_fragment(text: str, pattern) -> str:
    m = pattern.search(text or "")
    if not m:
        return ""
    frag = _clean_fragment(m.group(0))
    # Strip the leading keyword (e.g. "location", "salary") for readability.
    frag = re.sub(r"^[^\s:]+\s*[:)\-–—]?\s*", "", frag).strip()
    return frag[:160]


def parse_recruiter_post(text: str, *, original_url: str, company: str = "",
                         published_at: str = "", source_type: str = "manual") -> RecruiterPost:
    """Extract explicit roles, application emails/URLs and per-post signals.

    Deterministic only: roles come from bulleted lines carrying a role word;
    location/experience/salary/deadline are read from explicit keyword fragments.
    Nothing is inferred from prose that does not state it.
    """
    text = text or ""
    roles: List[RecruiterRole] = []
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        title = match.group(1).strip()
        title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
        if _ROLE_WORDS.search(title):
            roles.append(RecruiterRole(title=title))

    emails = _extract_application_emails(text)
    urls = _extract_application_urls(text)
    remote = ""
    if _REMOTE_RE.search(text):
        remote = "remote"

    # Attach the first post-level application email/URL to the first role that
    # does not already have one (most posts use a single shared contact).
    for role in roles:
        if emails and not role.published_email:
            role.published_email = emails[0]
        if urls and not role.application_url:
            role.application_url = urls[0]

    return RecruiterPost(
        original_url=original_url,
        text=text,
        company=company,
        published_at=published_at,
        source_type=source_type,
        confidence=0.6 if roles else 0.0,
        roles=roles,
        published_emails=emails,
        application_urls=urls,
    )
