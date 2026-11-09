"""Bridge from company discovery to the outreach pipeline (Part 2).

Qualifies each discovered company into one of four tiers, backed by evidence
and source URLs, then loads it into the outreach database:

* ``active_vacancy``      — a matching junior-engineering opening exists.
* ``high_signal_speculative`` — no vacancy, but credible recent hiring /
  expansion / Mumbai / India-remote signals.
* ``research_candidate``  — technically relevant, insufficient hiring evidence.
* ``unqualified``         — unrelated, inactive, or unsuitable.

Funding, "uses Kubernetes", or a YC listing alone do NOT count as hiring
intent; they only contribute to a research-candidate signal.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .company import (
    CAT_ACTIVE_HIRING, Company, CompanyStorage, REMOTE_INDIA, REMOTE_WORLDWIDE,
)
from .models import normalize_company
from .outreach import (
    ACTIVE_VACANCY, HIGH_SIGNAL, OutreachStorage, RESEARCH_CANDIDATE, UNQUALIFIED,
)

log = logging.getLogger("automatejob")


def _relevant_opening(jobs_storage, company_name: str) -> bool:
    if jobs_storage is None:
        return False
    n = normalize_company(company_name)
    row = jobs_storage.conn.execute(
        "SELECT 1 FROM jobs WHERE status='open' AND company_normalized=? "
        "AND (actionable=1 OR (location_preference='relocation' AND seniority IN ('junior','internship'))) "
        "LIMIT 1", (n,)).fetchone()
    return bool(row)


def qualify_company(company: Company, jobs_storage=None) -> str:
    """Return the four-tier qualification for a company."""
    has_opening = _relevant_opening(jobs_storage, company.name)
    if has_opening:
        return ACTIVE_VACANCY
    strong = (company.mumbai_office is True
              or company.remote_policy in (REMOTE_INDIA, REMOTE_WORLDWIDE)
              or (company.ats_type and company.ats_token))
    if strong:
        return HIGH_SIGNAL
    if company.technologies or (company.funding_stage and company.funding_stage != "unknown"):
        return RESEARCH_CANDIDATE
    return UNQUALIFIED


def build_shortlist(company_storage: CompanyStorage, jobs_storage,
                    outreach: OutreachStorage, limit: int = 50) -> int:
    """Qualify companies and load them into the outreach DB. Returns count loaded."""
    rows = company_storage.list(limit=10000)
    loaded = 0
    for r in rows:
        import json as _json
        company = Company(
            name=r["name"], website=r["website"], careers_url=r["careers_url"],
            ats_type=r["ats_type"], ats_token=r["ats_token"],
            mumbai_office=r["mumbai_office"], remote_policy=r["remote_policy"],
            funding_stage=r["funding_stage"], score=r["score"],
            technologies=_json.loads(r["technologies"] or "[]"),
            sources=_json.loads(r["sources"] or "[]"),
        )
        tier = qualify_company(company, jobs_storage)
        if tier == UNQUALIFIED:
            continue
        evidence = []
        if company.mumbai_office is True:
            evidence.append({"kind": "mumbai_office", "fact": "Mumbai office",
                             "url": (company.sources or [""])[0]})
        if company.remote_policy in (REMOTE_INDIA, REMOTE_WORLDWIDE):
            evidence.append({"kind": "remote", "fact": f"remote={company.remote_policy}",
                             "url": (company.sources or [""])[0]})
        if company.ats_type:
            evidence.append({"kind": "ats", "fact": f"{company.ats_type} board",
                             "url": company.careers_url})
        outreach.upsert_company(company.dedup_key, company.name, company.domain,
                                tier, company.score, evidence)
        loaded += 1
        if loaded >= limit:
            break
    return loaded
