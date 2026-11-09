"""Job fit scoring with explainable reasons.

The score ranks jobs *within* their qualification bucket; it does NOT decide
actionability. Actionability is a hard gate computed by qualification.py
(open + target role family + junior seniority + acceptable location). The
score's role factor is title-driven: a non-engineering title never earns role
points just because its description mentions engineering keywords.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .config import AppConfig, LocationConfig, RoleConfig, ScoringConfig
from .eligibility import (
    CONFIRMED,
    EXCLUDED,
    NEEDS_VERIFICATION,
    PREF_MUMBAI,
    PREF_RELOCATION,
    PREF_REMOTE,
    classify_eligibility,
)
from .models import Job
from .role_family import (
    NON_ENGINEERING,
    OTHER_ENGINEERING,
    REVIEW,
    TARGET_FAMILIES,
    classify_role_family,
)
from .seniority import (
    INTERMEDIATE,
    INTERNSHIP,
    JUNIOR,
    LEAD,
    SENIOR,
    UNKNOWN,
    classify_seniority,
)


def _keyword_hits(keyword: str, text: str) -> bool:
    """Match a keyword in text.

    Very short tokens ("go", "ai", "ml", "sre") are common substrings of
    unrelated words, so they require a word boundary; longer keywords match
    as substrings to catch hyphenated or plural forms.
    """
    if re.fullmatch(r"[a-z0-9]{1,3}", keyword):
        return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
    return keyword in text


class Scorer:
    def __init__(self, config: AppConfig):
        self.roles: List[RoleConfig] = config.roles
        self.locations: List[LocationConfig] = config.locations
        self.w: ScoringConfig = config.scoring
        self.config = config

    def _ensure_classified(self, job: Job) -> None:
        """Fill missing classification fields (idempotent; collector pre-sets them)."""
        if not job.role_family:
            job.role_family = classify_role_family(job)
        if not job.seniority:
            job.seniority = classify_seniority(job)
        if not job.eligibility:
            res = classify_eligibility(job, self.config)
            job.eligibility = res.status
            job.eligibility_reason = res.reason
            job.location_preference = res.preference

    def score(self, job: Job) -> Tuple[float, List[dict]]:
        """Return (score, reasons). Mutates job.score / job.score_reasons too."""
        self._ensure_classified(job)

        if job.eligibility == EXCLUDED:
            job.score = 0.0
            job.score_reasons = [{
                "factor": "eligibility", "points": 0.0,
                "detail": f"excluded: {job.eligibility_reason}",
            }]
            return job.score, job.score_reasons

        role_points, role_reasons = self._score_role(job)
        loc_points, loc_reasons = self._score_location(job)
        sen_points, sen_reasons = self._score_seniority(job)
        int_points, int_reasons = self._score_internship(job)

        reasons = role_reasons + loc_reasons + sen_reasons + int_reasons
        total = role_points + loc_points + sen_points + int_points

        if role_points == 0.0 and int_points == 0.0 and total > self.w.irrelevant_cap:
            total = float(self.w.irrelevant_cap)
            reasons.append({
                "factor": "gate", "points": 0.0,
                "detail": (f"no target-role match (and not an internship) — "
                           f"capped at {self.w.irrelevant_cap}"),
            })

        score = max(0.0, min(100.0, round(total, 1)))
        job.score = score
        job.score_reasons = reasons
        return score, reasons

    # -- factors -----------------------------------------------------------

    def _score_role(self, job: Job) -> Tuple[float, List[dict]]:
        family = job.role_family
        if family == NON_ENGINEERING:
            return 0.0, [{"factor": "role", "points": 0.0,
                          "detail": f"non-engineering title (family={family})"}]
        if family not in TARGET_FAMILIES:
            label = "other engineering" if family == OTHER_ENGINEERING else ("ambiguous" if family == REVIEW else family)
            return 0.0, [{"factor": "role", "points": 0.0,
                          "detail": f"{label} title (not a target engineering family)"}]

        title = (job.title or "").lower()
        description = (job.description or "").lower()

        best_role: Optional[RoleConfig] = None
        best_title_hits: List[str] = []
        best_desc_hits: List[str] = []

        for role in self.roles:
            t_hits = [kw for kw in role.keywords if _keyword_hits(kw, title)]
            d_hits = [kw for kw in role.keywords if _keyword_hits(kw, description) and kw not in t_hits]
            cur = (len(t_hits) + len(d_hits), len(t_hits))
            best = (len(best_title_hits) + len(best_desc_hits), len(best_title_hits))
            if cur > best:
                best_role, best_title_hits, best_desc_hits = role, t_hits, d_hits

        if best_title_hits:
            points = (self.w.role_base
                      + self.w.role_per_title * (len(best_title_hits) - 1)
                      + self.w.role_per_desc * len(best_desc_hits))
            points = min(float(points), float(self.w.role_max))
            shown = (best_title_hits + best_desc_hits)[:6]
            detail = f"matched {best_role.label} -> {', '.join(shown)}"
            return float(points), [{"factor": "role", "points": float(points), "detail": detail}]
        if best_desc_hits:
            points = min(float(self.w.role_per_desc * len(best_desc_hits)),
                         float(self.w.role_max))
            detail = f"matched {best_role.label} in description -> {', '.join(best_desc_hits[:6])}"
            return points, [{"factor": "role", "points": points, "detail": detail}]
        return 0.0, [{"factor": "role", "points": 0.0, "detail": "no target-role keywords matched"}]

    def _score_location(self, job: Job) -> Tuple[float, List[dict]]:
        status = job.eligibility
        pref = job.location_preference

        if status == CONFIRMED:
            if pref == PREF_MUMBAI:
                pts = self._loc_points("mumbai", float(self.w.location_max))
                return pts, [{"factor": "location", "points": pts,
                              "detail": "Mumbai metro (preferred)"}]
            if pref == PREF_REMOTE:
                pts = float(self.w.remote_open_points)
                return pts, [{"factor": "location", "points": pts,
                              "detail": "India-eligible / worldwide remote"}]
            if pref == PREF_RELOCATION:
                pts = float(self.w.relocation_points)
                return pts, [{"factor": "location", "points": pts,
                              "detail": "other India city (relocation queue)"}]
            return 0.0, [{"factor": "location", "points": 0.0,
                          "detail": "eligible but no location detail"}]

        if status == NEEDS_VERIFICATION:
            pts = float(self.w.needs_verification_points)
            return pts, [{"factor": "location", "points": pts,
                          "detail": f"needs verification: {job.eligibility_reason}"}]

        return 0.0, [{"factor": "location", "points": 0.0,
                      "detail": f"excluded: {job.eligibility_reason}"}]

    def _loc_points(self, key: str, default: float) -> float:
        for cfg in self.locations:
            if cfg.key == key:
                return float(cfg.points)
        return default

    def _score_seniority(self, job: Job) -> Tuple[float, List[dict]]:
        level = job.seniority or UNKNOWN

        if level in (INTERNSHIP, JUNIOR):
            p = float(self.w.junior_bonus)
            return p, [{"factor": "seniority", "points": p,
                        "detail": f"{level} role (junior target)"}]
        if level == INTERMEDIATE:
            return 0.0, [{"factor": "seniority", "points": 0.0,
                          "detail": "intermediate level (above junior target)"}]
        if level in (SENIOR, LEAD):
            p = float(self.w.senior_penalty)
            return p, [{"factor": "seniority", "points": p,
                        "detail": f"{level} level (above junior target)"}]
        if level == REVIEW:
            return 0.0, [{"factor": "seniority", "points": 0.0,
                          "detail": "borderline seniority — review"}]
        return 0.0, [{"factor": "seniority", "points": 0.0,
                      "detail": "no seniority signal — uncertain (review)"}]

    def _score_internship(self, job: Job) -> Tuple[float, List[dict]]:
        if job.seniority == INTERNSHIP:
            p = float(self.w.internship_max)
            return p, [{"factor": "internship", "points": p,
                        "detail": "internship/co-op role"}]
        return 0.0, [{"factor": "internship", "points": 0.0, "detail": "not an internship"}]
