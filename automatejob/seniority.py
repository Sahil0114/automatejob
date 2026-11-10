"""Seniority classification for job postings.

Requirement: prioritise internships, graduates, freshers, trainees, juniors,
entry-level and credible 0–2 years roles; identify intermediate, senior,
staff, lead and manager levels; and keep borderline roles in *review* rather
than auto-rejecting them.

Levels produced (stored on the job and used for both ranking and filters):

* ``internship``   — intern / co-op / trainee programmes.
* ``junior``       — junior / entry-level / new-grad / fresher / 0–2 years.
* ``intermediate`` — mid-level, engineer II/2, 3–5 years.
* ``senior``       — senior / staff / principal / architect.
* ``lead``         — lead / manager / director / head of / VP / CTO.
* ``review``       — borderline (mixed signals, or an ambiguous experience
                     band) — never auto-accepted or auto-rejected.
* ``unknown``      — no signal found; treated as junior-eligible for ranking
                     but NOT asserted as a confirmed junior match.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import Job

INTERNSHIP = "internship"
JUNIOR = "junior"
INTERMEDIATE = "intermediate"
SENIOR = "senior"
LEAD = "lead"
REVIEW = "review"
UNKNOWN = "unknown"

ALL_LEVELS = (INTERNSHIP, JUNIOR, INTERMEDIATE, SENIOR, LEAD, REVIEW, UNKNOWN)

# Titles that identify a leadership/management track (above "senior").
_LEAD_TERMS = [
    "lead", "manager", "director", "head of", "vice president", "vp", "cto",
    "chief",
]
# Staff-and-above individual-contributor levels.
_SENIOR_TERMS = [
    "senior", "staff", "principal", "architect", "sr", "sr.", "staff+",
    "distinguished", "fellow",
]
# Intermediate / mid-level markers.
_INTERMEDIATE_TERMS = [
    "mid-level", "mid level", "intermediate",
]
# Junior / entry markers.
_JUNIOR_TERMS = [
    "junior", "entry-level", "entry level", "new grad", "new graduate",
    "graduate", "fresher", "trainee", "early career", "associate",
    "0-2", "0 - 2", "1-2", "1 - 2", "up to 2 years", "0 to 2",
]
_INTERN_TERMS = ["intern", "internship", "co-op", "coop", "co op"]

# A credible "0–2 years" experience band in the text -> junior.
# NOTE: "2+ years" is deliberately EXCLUDED — it means "at least 2 years",
# which is the upper edge of the junior band and excludes most fresh grads;
# it maps to REVIEW (borderline) instead.
_JUNIOR_EXPERIENCE = re.compile(
    r"\b(?:0|1|2)\s*[-–—]\s*(?:1|2)\s*(?:years?|yrs?)\b"
    r"|\b(?:0|1)\s*(?:\+\s*)\s*(?:years?|yrs?)\b"
    r"|\bup\s+to\s+2\s*(?:\+\s*)?\s*years\b",
    re.IGNORECASE,
)
# "2+ years" / "2+ yrs" — the boundary between junior and mid-level -> review.
_BORDERLINE_EXPERIENCE = re.compile(
    r"\b2\s*(?:\+\s*)\s*(?:years?|yrs?)\b"
    r"|\b2\s*[-–—]\s*3\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
# 3–5 years -> intermediate.
_INTERMEDIATE_EXPERIENCE = re.compile(
    r"\b(?:3|4|5)\s*[-–—]\s*(?:4|5|6)\s*years?\b"
    r"|\b(?:2)\s*[-–—]\s*(?:4|5)\s*years?\b"
    r"|\b(?:3|4|5)\s*(?:\+\s*)\s*years?\b",
    re.IGNORECASE,
)
# 6+ years -> senior.
_SENIOR_EXPERIENCE = re.compile(
    r"\b(?:6|7|8|9|1[0-9]|2[0-9])\s*(?:\+\s*)?\s*years?\b",
    re.IGNORECASE,
)

# Level-coded titles like "Software Engineer 2" / "Engineer III".
_LEVEL_CODE = re.compile(r"\b(?:engineer|sre|developer|scientist)\s+(i{1,3}|\d)\b",
                         re.IGNORECASE)

# BigTech-style level codes "L3"/"E4" (Google L3 = entry, but ambiguous across
# firms; handled conservatively).
_LEVEL_L = re.compile(r"\b[le]([2-9])\b", re.IGNORECASE)


def _has(terms, text: str) -> bool:
    """Word-boundary match for any seniority term.

    Word boundaries prevent prefix false-positives ("intern" matching
    "internet"/"internal", "lead" matching "leader").
    """
    for t in terms:
        if re.search(r"\b" + re.escape(t) + r"\b", text):
            return True
    return False


def classify_seniority(job: Job) -> str:
    """Return a seniority level for the job based on title + description + type."""
    title = (job.title or "").lower()
    desc = (job.description or "").lower()
    emp = (job.employment_type or "").lower()
    text = f"{title} {emp}".strip()
    full = f"{title} {desc} {emp}".strip()

    is_intern = _has(_INTERN_TERMS, text) or _has(_INTERN_TERMS, emp)
    has_junior = _has(_JUNIOR_TERMS, text)
    has_senior = _has(_SENIOR_TERMS, text)
    has_lead = _has(_LEAD_TERMS, text)
    has_intermediate = _has(_INTERMEDIATE_TERMS, title)
    # "Founding X Engineer" is a high-bar early hire, not a junior role.
    has_founding = _has(["founding"], title)

    # Experience bands in the description (title experience is rare, but check both).
    junior_exp = bool(_JUNIOR_EXPERIENCE.search(full))
    borderline_exp = bool(_BORDERLINE_EXPERIENCE.search(full))
    intermediate_exp = bool(_INTERMEDIATE_EXPERIENCE.search(full))
    senior_exp = bool(_SENIOR_EXPERIENCE.search(full))

    # Internships first: explicit intern/co-op beats everything.
    if is_intern:
        return INTERNSHIP

    # Leadership/management always outranks IC seniority markers.
    if has_lead and not has_junior:
        return LEAD
    if has_senior and not has_junior:
        if senior_exp or not (junior_exp or intermediate_exp):
            return SENIOR
        # "senior" in title but a junior-looking band -> review.
        return REVIEW

    # Conflicting markers (junior + senior/lead) -> review.
    if has_junior and (has_senior or has_lead):
        return REVIEW

    if has_junior:
        return JUNIOR

    if has_intermediate:
        return INTERMEDIATE

    # "Founding" without an explicit senior/lead marker -> review (high-bar early
    # hire, not a confirmed junior role).
    if has_founding:
        return REVIEW

    # Experience-band-based calls: the HIGHEST band wins. A JD listing
    # "4+ years of Go" alongside "2+ years of TypeScript" is mid/senior, not
    # junior — the junior band must not override a higher primary requirement.
    if senior_exp:
        return SENIOR
    if intermediate_exp:
        return INTERMEDIATE
    if junior_exp:
        return JUNIOR
    if borderline_exp:
        return REVIEW

    # Level-coded "Engineer 3" / "Engineer II" that isn't caught above.
    m = _LEVEL_CODE.search(title)
    if m:
        code = m.group(1)
        if code in ("i", "1"):
            return JUNIOR
        if code in ("ii", "2"):
            return INTERMEDIATE
        return SENIOR  # "3"/"III" -> mid/senior, clearly not junior

    # BigTech level codes "L3"/"E4". L3 (entry at some firms) is ambiguous -> review.
    m = _LEVEL_L.search(title)
    if m:
        n = int(m.group(1))
        if n == 2:
            return INTERMEDIATE
        if n == 3:
            return REVIEW
        return SENIOR  # L4+ / E4+

    return UNKNOWN


def seniority_is_junior_target(level: str) -> bool:
    """True for levels the search actively prioritises."""
    return level in (INTERNSHIP, JUNIOR)


def seniority_gate(level: str) -> str:
    """Map a seniority level to an actionable gate.

    * "actionable" — intern/new-grad/fresher/trainee/junior/0-2 years.
    * "review"     — uncertain (no signal, or borderline).
    * "gated"      — intermediate/senior/staff/lead/manager.
    """
    if level in (INTERNSHIP, JUNIOR):
        return "actionable"
    if level in (INTERMEDIATE, SENIOR, LEAD):
        return "gated"
    return "review"  # REVIEW and UNKNOWN
