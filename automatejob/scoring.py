"""Job fit scoring with explainable reasons.

Scores a Job 0..100 against the configured target profile across four
independent factors. Every factor appends a human-readable reason.

The score is computed AFTER the hard eligibility filter (see eligibility.py):
excluded jobs are never scored (the collector assigns them 0), and
needs_verification jobs are scored but kept out of the confirmed list.

Calibration targets: a strong match (right role + Mumbai/India-remote +
junior) scores ~65-80; an intermediate/senior or wrong-location role scores
lower; an irrelevant role scores ~0-15.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .config import AppConfig, LocationConfig, RoleConfig, ScoringConfig
from .eligibility import (
    CONFIRMED,
    EXCLUDED,
    NEEDS_VERIFICATION,
    analyze_location,
    classify_eligibility,
)
from .models import Job
from .seniority import (
    INTERMEDIATE,
    INTERNSHIP,
    JUNIOR,
    LEAD,
    REVIEW,
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

    def ensure_eligibility(self, job: Job) -> str:
        """Fill in job.eligibility / reason / seniority if not already set."""
        if not job.eligibility:
            res = classify_eligibility(job, self.config)
            job.eligibility = res.status
            job.eligibility_reason = res.reason
        if not job.seniority:
            job.seniority = classify_seniority(job)
        return job.eligibility

    def score(self, job: Job) -> Tuple[float, List[dict]]:
        """Return (score, reasons). Mutates job.score / job.score_reasons too."""
        status = self.ensure_eligibility(job)

        # Hard filter: excluded jobs never get scored into the ranking.
        if status == EXCLUDED:
            job.score = 0.0
            job.score_reasons = [{
                "factor": "eligibility", "points": 0.0,
                "detail": f"excluded: {job.eligibility_reason}",
            }]
            return job.score, job.score_reasons

        role_points, role_reasons = self._score_role(job)
        loc_points, loc_reasons = self._score_location(job, status)
        sen_points, sen_reasons = self._score_seniority(job)
        int_points, int_reasons = self._score_internship(job)

        reasons = role_reasons + loc_reasons + sen_reasons + int_reasons
        total = role_points + loc_points + sen_points + int_points

        # A role with no target-role match AND no internship signal is not a
        # target role at all: cap it so pure location/seniority points can't
        # push an irrelevant role (e.g. a recruiter in Mumbai) high.
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

    def _score_location(self, job: Job, status: str) -> Tuple[float, List[dict]]:
        a = analyze_location(job, self.config)

        if status == CONFIRMED:
            if a.preferred_city:
                pts = self._loc_points("mumbai", float(self.w.location_max))
                return pts, [{"factor": "location", "points": pts,
                              "detail": f"preferred city '{a.preferred_city}' (eligible)"}]
            if a.has_india:
                pts = self._loc_points("india", 30.0)
                detail = (f"India location '{a.india_city}' (India-wide eligible)" if a.india_city
                          else "India location (eligible)")
                return pts, [{"factor": "location", "points": pts, "detail": detail}]
            if a.is_remote:
                pts = float(self.w.remote_open_points)
                reasons = [{"factor": "location", "points": pts,
                            "detail": "worldwide/India remote (eligible)"}]
                if a.has_india:
                    bonus = float(self.w.remote_india_bonus)
                    pts += bonus
                    reasons.append({"factor": "location", "points": bonus,
                                    "detail": "remote role open to India"})
                return pts, reasons
            # confirmed but no location subtype (shouldn't happen)
            return 0.0, [{"factor": "location", "points": 0.0,
                          "detail": "eligible but no location detail"}]

        if status == NEEDS_VERIFICATION:
            if a.is_remote:
                pts = float(self.w.needs_verification_points)
                return pts, [{"factor": "location", "points": pts,
                              "detail": f"remote, unclear eligibility ({a.ambiguous_region or 'unspecified'})"}]
            return 0.0, [{"factor": "location", "points": 0.0,
                          "detail": f"needs verification: {job.eligibility_reason}"}]

        # EXCLUDED
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
                          "detail": "intermediate level (above junior target, kept for review)"}]
        if level in (SENIOR, LEAD):
            p = float(self.w.senior_penalty)
            return p, [{"factor": "seniority", "points": p,
                        "detail": f"{level} level (above junior target)"}]
        if level == REVIEW:
            return 0.0, [{"factor": "seniority", "points": 0.0,
                          "detail": "borderline seniority — review"}]
        p = float(self.w.neutral_bonus)
        return p, [{"factor": "seniority", "points": p,
                    "detail": "no seniority marker (junior-eligible)"}]

    def _score_internship(self, job: Job) -> Tuple[float, List[dict]]:
        if job.seniority == INTERNSHIP:
            p = float(self.w.internship_max)
            return p, [{"factor": "internship", "points": p,
                        "detail": "internship/co-op role"}]
        return 0.0, [{"factor": "internship", "points": 0.0, "detail": "not an internship"}]
