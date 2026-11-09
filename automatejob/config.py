"""Configuration loading (TOML, via the standard library)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_USER_AGENT = (
    "automatejob/0.1 (personal job search assistant; "
    "reads public job-board APIs only)"
)


@dataclass
class SourceConfig:
    type: str                 # 'greenhouse' | 'lever'
    token: str                # board token / company slug
    name: str = ""            # display name / fallback company


@dataclass
class RoleConfig:
    key: str
    label: str
    keywords: List[str]


@dataclass
class LocationConfig:
    key: str
    label: str
    keywords: List[str]
    points: int


@dataclass
class ScoringConfig:
    role_max: int = 40
    role_base: int = 18
    role_per_title: int = 4
    role_per_desc: int = 2
    location_max: int = 35
    remote_open_points: int = 15
    remote_restricted_penalty: int = -15
    remote_india_bonus: int = 4
    needs_verification_points: int = 5  # ambiguous-remote roles, kept below confirmed
    relocation_points: int = 10         # other-India cities (relocation queue)
    seniority_max: int = 15
    internship_max: int = 10
    junior_bonus: int = 15
    senior_penalty: int = -15
    neutral_bonus: int = 8
    irrelevant_cap: int = 20


@dataclass
class EligibilityConfig:
    """Location & relocation preferences (kept separate from employment eligibility)."""
    cities: List[str] = field(default_factory=lambda: ["mumbai", "thane", "navi mumbai"])
    relocation: bool = True       # accept other India cities into a relocation queue


@dataclass
class AppConfig:
    sources: List[SourceConfig]
    roles: List[RoleConfig]
    locations: List[LocationConfig]
    scoring: ScoringConfig
    eligibility: EligibilityConfig
    db_path: str = "data/jobs.db"
    companies_path: str = "data/companies.toml"
    companies_db_path: str = "data/companies.db"
    outreach_db_path: str = "data/outreach.db"
    reports_dir: str = "reports"
    min_score: float = 40.0
    list_limit: int = 20
    close_after_scans: int = 2
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

        sources = [
            SourceConfig(
                type=s.get("type", "").lower(),
                token=str(s.get("token", "")),
                name=s.get("name", ""),
            )
            for s in raw.get("sources", [])
            if s.get("type") and s.get("token")
        ]

        roles = [
            RoleConfig(
                key=r.get("key", ""),
                label=r.get("label", r.get("key", "")),
                keywords=[str(k) for k in r.get("keywords", [])],
            )
            for r in raw.get("roles", [])
            if r.get("key") and r.get("keywords")
        ]

        locations = [
            LocationConfig(
                key=l.get("key", ""),
                label=l.get("label", l.get("key", "")),
                keywords=[str(k) for k in l.get("keywords", [])],
                points=int(l.get("points", 0)),
            )
            for l in raw.get("locations", [])
            if l.get("key") and l.get("keywords")
        ]

        scoring = ScoringConfig(**raw.get("scoring", {}))
        database = raw.get("database", {})
        collect = raw.get("collect", {})
        tracking = raw.get("tracking", {})
        elig_raw = raw.get("eligibility", {})
        eligibility = EligibilityConfig(
            cities=[str(c) for c in elig_raw.get("cities", ["mumbai", "thane", "navi mumbai"])],
            relocation=bool(elig_raw.get("relocation", True)),
        )

        return cls(
            sources=sources,
            roles=roles,
            locations=locations,
            scoring=scoring,
            eligibility=eligibility,
            db_path=database.get("path", "data/jobs.db"),
            companies_path=raw.get("companies", {}).get("path", "data/companies.toml"),
            companies_db_path=raw.get("companies", {}).get("db", "data/companies.db"),
            outreach_db_path=raw.get("outreach", {}).get("db", "data/outreach.db"),
            reports_dir=raw.get("report", {}).get("dir", "reports"),
            min_score=float(collect.get("min_score", 40.0)),
            list_limit=int(collect.get("list_limit", 20)),
            close_after_scans=int(tracking.get("close_after_scans", 2)),
            user_agent=raw.get("user_agent", DEFAULT_USER_AGENT),
        )
