"""Actionable qualification — the single gate every shortlist entry must pass.

A job is **actionable** only if it passes ALL of:

1. it is open (not closed),
2. its title matches a target engineering role family,
3. its seniority is junior-target (intern/new-grad/fresher/trainee/junior/0-2y),
4. its employment eligibility is confirmed (India-eligible), AND
5. its location preference is acceptable (Mumbai metro onsite/hybrid, or
   India-eligible / world-wide remote).

Anything else is *not* actionable. Other India cities (Bengaluru/Hyderabad/
Gurugram) sit in a separate **relocation queue**; ambiguous remote and
unclear-seniority roles sit in **review**; non-engineering and senior roles
are simply not shortlisted. No quota-padding: if few jobs pass, few are shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .config import AppConfig
from .eligibility import (
    CONFIRMED,
    NEEDS_VERIFICATION,
    PREF_MUMBAI,
    PREF_RELOCATION,
    PREF_REMOTE,
    classify_eligibility,
)
from .models import Job
from .role_family import TARGET_FAMILIES, classify_role_family
from .seniority import INTERNSHIP, JUNIOR, classify_seniority

ACTIONABLE = "actionable"
RELOCATION = "relocation"
REVIEW = "review"
EXCLUDED = "excluded"


@dataclass
class Classification:
    role_family: str = ""
    seniority: str = ""
    eligibility: str = ""
    eligibility_reason: str = ""
    location_preference: str = ""
    actionable: bool = False
    reasons: List[str] = field(default_factory=list)


def classify_job(job: Job, config: AppConfig, registry=None) -> Classification:
    """Classify a job across all gates and decide actionability. Mutates the
    Job's classification fields so the caller can store them."""
    role_family = classify_role_family(job)
    seniority = classify_seniority(job)
    elig = classify_eligibility(job, config, registry)

    job.role_family = role_family
    job.seniority = seniority
    job.eligibility = elig.status
    job.eligibility_reason = elig.reason
    job.location_preference = elig.preference

    reasons: List[str] = []
    fails: List[str] = []

    # Gate 1: engineering role family (title-driven).
    if role_family in TARGET_FAMILIES:
        reasons.append(f"role family: {role_family}")
    else:
        fails.append(f"role family {role_family} not a target engineering family")

    # Gate 2: junior-target seniority.
    if seniority in (INTERNSHIP, JUNIOR):
        reasons.append(f"seniority: {seniority} (junior target)")
    else:
        fails.append(f"seniority {seniority} not junior target")

    # Gate 3: employment eligibility.
    if elig.status == CONFIRMED:
        reasons.append("India-eligible")
    else:
        fails.append(f"eligibility {elig.status}: {elig.reason}")

    # Gate 4: location preference.
    if elig.preference == PREF_MUMBAI:
        reasons.append("location: Mumbai metro")
    elif elig.preference == PREF_REMOTE:
        reasons.append("location: eligible remote")
    else:
        fails.append(f"location preference '{elig.preference or 'none'}' not acceptable")

    actionable = not fails
    reasons = reasons + (["gates passed"] if actionable else fails)
    job.actionable = 1 if actionable else 0
    return Classification(
        role_family=role_family,
        seniority=seniority,
        eligibility=elig.status,
        eligibility_reason=elig.reason,
        location_preference=elig.preference,
        actionable=actionable,
        reasons=reasons,
    )


def is_actionable(job: Job, config: AppConfig, registry=None) -> bool:
    """Convenience wrapper returning just the boolean."""
    return classify_job(job, config, registry).actionable
