"""Data model and normalization helpers for job postings."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Company suffix tokens stripped from the end of a company name so that
# "Cloudflare, Inc." and "Cloudflare" normalize to the same value.
_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "gmbh", "pvt", "private", "plc", "bv", "ag", "srl", "sa", "technologies",
    "technology", "labs", "llp", "inc.", "ltd.", "llc.", "co.", "corp.",
}


def normalize_company(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, drop trailing suffixes."""
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    tokens = s.split()
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", s).strip()


# Tracking params that do not identify a posting and are safe to drop from a
# canonical URL. The query string is otherwise PRESERVED: some feeds (e.g.
# Databricks on Greenhouse) encode the unique job id in the query (?gh_jid=N).
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "referrer",
}


def normalize_url(url: str) -> str:
    """Canonicalize a URL: lowercase, drop fragment/trailing slash/tracking params.

    The query string is kept (minus pure tracking params) because some job
    boards put the posting id there; dropping it would collapse distinct jobs.
    """
    parts = urlsplit((url or "").strip().lower())
    query = parts.query
    if query:
        qs = parse_qsl(query, keep_blank_values=True)
        qs = [(k, v) for (k, v) in qs if k.lower() not in _TRACKING_PARAMS]
        query = urlencode(qs)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def make_dedup_key(company: str, title: str, url: str) -> str:
    """Deterministic per-source dedup key (company + title + source URL)."""
    joined = "|".join([normalize_company(company), normalize_title(title), normalize_url(url)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def make_cross_board_key(company: str, title: str, location: str = "") -> str:
    """Key identical for the same role (company+title+location) listed on two
    boards. Used for duplicate reporting and to collapse the qualified list.
    """
    loc = re.sub(r"[^\w\s]", " ", (location or "").lower())
    loc = re.sub(r"\s+", " ", loc).strip()
    joined = "|".join([normalize_company(company), normalize_title(title), loc])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def make_content_hash(title: str, description: str, location: str) -> str:
    """Hash of the mutable posting content, to detect changed postings."""
    joined = "\n".join([title or "", description or "", location or ""])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class Job:
    """A single job posting as extracted from a source adapter."""

    source_type: str                 # 'greenhouse' | 'lever' | 'ashby'
    source_token: str                # board token / company slug
    external_id: str                 # posting id assigned by the source
    title: str
    company: str
    location: str
    url: str                         # canonical public job-board URL
    apply_url: str = ""
    workplace_type: str = ""         # 'remote' | 'hybrid' | 'onsite' | ''
    employment_type: str = ""        # 'Full-time' | 'Internship' | 'Contract' | ''
    posted_at: str = ""              # ISO-8601 timestamp (or '' when unknown)
    description: str = ""            # plain-text description (HTML converted)
    secondary_locations: list = field(default_factory=list)
    score: float = 0.0
    score_reasons: list = field(default_factory=list)
    eligibility: str = ""            # confirmed | excluded | needs_verification
    eligibility_reason: str = ""
    seniority: str = ""              # level from seniority.py
    role_family: str = ""            # engineering family from role_family.py
    location_preference: str = ""    # mumbai | relocation | remote
    actionable: int = 0              # 1 when all gates pass

    @property
    def company_normalized(self) -> str:
        return normalize_company(self.company)

    @property
    def title_normalized(self) -> str:
        return normalize_title(self.title)

    @property
    def url_normalized(self) -> str:
        return normalize_url(self.url)

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(self.company, self.title, self.url)

    @property
    def cross_board_key(self) -> str:
        return make_cross_board_key(self.company, self.title, self.location)

    @property
    def content_hash(self) -> str:
        return make_content_hash(self.title, self.description, self.location)

    def to_row(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_token": self.source_token,
            "external_id": self.external_id,
            "title": self.title,
            "title_normalized": self.title_normalized,
            "company": self.company,
            "company_normalized": self.company_normalized,
            "location": self.location,
            "url": self.url,
            "url_normalized": self.url_normalized,
            "apply_url": self.apply_url,
            "workplace_type": self.workplace_type,
            "employment_type": self.employment_type,
            "posted_at": self.posted_at,
            "description": self.description,
            "secondary_locations": self.secondary_locations,
            "score": self.score,
            "score_reasons": self.score_reasons,
            "dedup_key": self.dedup_key,
            "cross_board_key": self.cross_board_key,
            "content_hash": self.content_hash,
            "eligibility": self.eligibility,
            "eligibility_reason": self.eligibility_reason,
            "seniority": self.seniority,
            "role_family": self.role_family,
            "location_preference": self.location_preference,
            "actionable": self.actionable,
        }
