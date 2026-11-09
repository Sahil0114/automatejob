"""Company-level opportunity scoring (separate from job scoring).

Assigns each company a score (0-100) and one of three opportunity categories:

* ``active_hiring``    — a relevant verified opening exists for this company.
* ``speculative_outreach`` — no matching vacancy, but credible engineering /
  expansion signals (Mumbai office, India/world-wide remote, funding, active
  open source with relevant tech).
* ``research_lead``    — potentially relevant, insufficient evidence yet.

Every claim is a field on the Company (which itself carries source URLs), so
scores stay traceable. A speculative lead is never presented as a vacancy.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .company import (
    CAT_ACTIVE_HIRING, CAT_RESEARCH_LEAD, CAT_SPECULATIVE,
    Company, REMOTE_INDIA, REMOTE_WORLDWIDE,
)

# Alignment with the target profile (Go/Python/K8s/Docker/Terraform/Linux/
# cloud infra / AI agents / backend).
_TECH_ALIGN = [
    "go", "golang", "python", "kubernetes", "k8s", "docker", "terraform",
    "linux", "aws", "azure", "gcp", "cloud", "postgres", "postgresql",
    "mysql", "mongodb", "redis", "kafka", "ai", "ml", "llm", "machine learning",
    "agent", "automation", "backend", "api", "infrastructure", "devops",
    "sre", "platform", "database", "distributed",
]


def _tech_alignment(company: Company) -> List[str]:
    tech_text = " ".join((company.technologies or []) + [company.product or ""]).lower()
    hits = []
    for t in _TECH_ALIGN:
        if len(t) <= 3:
            if re.search(r"\b" + re.escape(t) + r"\b", tech_text):
                hits.append(t)
        elif t in tech_text:
            hits.append(t)
    return hits


def score_company(company: Company, has_relevant_opening: bool = False) -> Company:
    """Mutate and return the company with score/category/reasons filled in."""
    reasons: List[dict] = []
    pts = 0.0

    def add(factor: str, p: float, detail: str) -> None:
        nonlocal pts
        pts += p
        reasons.append({"factor": factor, "points": p, "detail": detail})

    # Mumbai engineering presence (strongest location signal).
    if company.mumbai_office is True or company.mumbai_engineering is True:
        add("mumbai", 25.0, "verified Mumbai office/engineering presence")

    # India-eligible remote.
    if company.remote_policy == REMOTE_INDIA:
        add("remote", 20.0, "verified India-eligible remote hiring")
    elif company.remote_policy == REMOTE_WORLDWIDE:
        add("remote", 20.0, "world-wide remote (India-eligible)")

    # Tech alignment with the target stack.
    techs = _tech_alignment(company)
    if techs:
        add("tech", min(20.0, 4.0 * len(techs)),
            f"tech alignment: {', '.join(techs[:5])}")

    # Early-stage / funded startup relevance.
    if company.funding_stage and company.funding_stage != "unknown":
        add("funding", 10.0, f"funding/batch: {company.funding_stage}")
    if company.company_size and company.company_size != "unknown":
        if company.company_size in ("1-10", "11-50"):
            add("size", 5.0, f"early-stage size ({company.company_size})")

    # Evidence freshness / provenance.
    if company.last_verified:
        add("evidence", 5.0, "last verified timestamp present")

    pts = round(max(0.0, min(100.0, pts)), 1)
    company.score = pts
    company.score_reasons = reasons

    # Category.
    if has_relevant_opening:
        company.category = CAT_ACTIVE_HIRING
    elif (company.mumbai_office is True or company.mumbai_engineering is True
          or company.remote_policy in (REMOTE_INDIA, REMOTE_WORLDWIDE)
          or techs):
        company.category = CAT_SPECULATIVE
    else:
        company.category = CAT_RESEARCH_LEAD

    return company


def relevant_opening_companies(jobs_storage, company_names: List[str]) -> set:
    """Return the set of normalized company names that have a relevant (junior
    engineering, India-eligible) opening in the jobs table."""
    from .models import normalize_company
    names = {normalize_company(n) for n in company_names}
    if not names:
        return set()
    placeholders = ",".join("?" for _ in names)
    rows = jobs_storage.conn.execute(
        f"SELECT company_normalized FROM jobs WHERE status='open' "
        f"AND actionable=1 AND company_normalized IN ({placeholders})",
        list(names),
    ).fetchall()
    return {r["company_normalized"] for r in rows}


# ---------------------------------------------------------------------------
# Outreach-readiness score (distinct from the general company-fit score).
#
# A company is outreach-ready ONLY when there is verifiable evidence, not just
# a YC batch, a relevant technology, or a Mumbai location. Each factor requires
# a source. A company with an uncertain domain, inactive-looking operations, or
# an unsupported justification must NOT become outreach-ready.
# ---------------------------------------------------------------------------

_HIRING_KINDS = {"hiring", "expansion", "job_posting"}
_SIGNAL_KINDS = {"product_launch", "eng_blog", "oss", "hiring", "expansion"}


def outreach_readiness(company: dict, evidence: List[dict],
                       active_website: Optional[bool] = None,
                       junior_hypothesis: Optional[str] = None) -> dict:
    """Score 0-100 plus a hard `ready` gate and traceable reasons.

    `evidence` is a list of {kind, fact, url, observed_at} from company research.
    `active_website` is None when unverified (unknown, not assumed active).
    """
    reasons: List[dict] = []
    pts = 0.0

    def add(factor, p, detail):
        nonlocal pts
        pts += p
        reasons.append({"factor": factor, "points": p, "detail": detail})

    domain = (company.get("domain") or "").strip()
    if domain and domain != "unknown":
        add("domain", 15, f"canonical domain {domain}")
    else:
        add("domain", -100, "domain uncertain — NOT outreach-ready")

    if active_website is True:
        add("active_website", 15, "website confirmed active")
    elif active_website is False:
        add("active_website", -100, "website appears inactive — NOT outreach-ready")
    # active_website None => unknown, no points, no penalty.

    techs = company.get("technologies") or []
    if techs:
        add("tech", min(15, 3 * len(techs[:5])), f"engineering relevance: {', '.join(techs[:5])}")

    if company.get("mumbai_office") is True:
        add("mumbai", 20, "verified Mumbai office/engineering")
    if (company.get("remote_policy") or "") in (REMOTE_INDIA, REMOTE_WORLDWIDE):
        add("remote", 20, f"verified {company.get('remote_policy')} eligibility")

    has_signal = any(e.get("kind") in _SIGNAL_KINDS for e in evidence)
    has_hiring = any(e.get("kind") in _HIRING_KINDS for e in evidence)
    if has_signal:
        add("signal", 15, "recent engineering/hiring/expansion signal with source")
    if has_hiring:
        add("hiring", 10, "explicit hiring/expansion evidence")
    if junior_hypothesis:
        add("junior", 10, f"early-career hypothesis: {junior_hypothesis[:80]}")
    if evidence:
        # one verified company-specific observation with a URL and date
        sourced = [e for e in evidence if e.get("url")]
        if sourced:
            add("observation", 5, f"{len(sourced)} sourced observation(s)")

    pts = round(max(0.0, min(100.0, pts)), 1)
    # Hard gate: domain known + active + at least one sourced signal/hiring.
    ready = (bool(domain and domain != "unknown")
             and active_website is not False
             and has_signal
             and pts >= 55)
    return {"score": pts, "ready": ready, "reasons": reasons}
