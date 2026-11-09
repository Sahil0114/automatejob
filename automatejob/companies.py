"""Normalized company registry.

A small, honest, verifiable registry of the companies we watch. Every claim
carries a source and a ``last_checked`` timestamp, and unknown facts are
recorded as ``null`` rather than guessed. The registry is used to enrich jobs
(e.g. a company known to hire world-wide remote upgrades a bare "Remote" role
from needs_verification to confirmed).

Never fabricate: only add a company after its board has been verified against
the live API and its location/eligibility facts have been checked against the
company's own careers page or the returned postings.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .models import normalize_company

# remote_eligibility vocabulary
WORLDWIDE_REMOTE = "worldwide_remote"   # hires remote anywhere, incl. India
INDIA_REMOTE = "india_remote"           # remote roles open to India
INDIA_OFFICE = "india_office"           # has an India office (on-site/hybrid)
RESTRICTED = "restricted"               # remote/onsite does not include India
UNKNOWN = "unknown"


@dataclass
class Company:
    name: str
    website: str = ""
    careers_url: str = ""
    ats_type: str = ""               # greenhouse | lever | ashby | ...
    mumbai_office: Optional[bool] = None
    remote_eligibility: str = UNKNOWN
    last_checked: str = ""           # ISO-8601 date ('' = not yet verified)
    sources: List[str] = field(default_factory=list)

    @property
    def normalized_name(self) -> str:
        return normalize_company(self.name)


class CompanyRegistry:
    def __init__(self, companies: List[Company]):
        self._by_name: Dict[str, Company] = {}
        for c in companies:
            self._by_name[c.normalized_name] = c
            # Also index by a token-only key for loose matching.
            if c.ats_type:
                self._by_name.setdefault(f"{c.normalized_name}:{c.ats_type}", c)

    def lookup(self, name: str) -> Optional[Company]:
        if not name:
            return None
        key = normalize_company(name)
        c = self._by_name.get(key)
        if c:
            return c
        # Fallback: prefix match (e.g. "Kong Inc." vs "kong").
        for k, comp in self._by_name.items():
            if k.split(":")[0] == key or k.split(":")[0].startswith(key) or key.startswith(k.split(":")[0]):
                return comp
        return None

    def __len__(self) -> int:
        return len(self._by_name)

    def all(self) -> List[Company]:
        seen = set()
        out = []
        for c in self._by_name.values():
            if id(c) not in seen:
                seen.add(id(c))
                out.append(c)
        return out

    @classmethod
    def load(cls, path: str | Path) -> "CompanyRegistry":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        companies = []
        for item in raw.get("companies", []):
            companies.append(Company(
                name=item.get("name", ""),
                website=item.get("website", ""),
                careers_url=item.get("careers_url", ""),
                ats_type=item.get("ats_type", ""),
                mumbai_office=item.get("mumbai_office"),
                remote_eligibility=item.get("remote_eligibility", UNKNOWN),
                last_checked=item.get("last_checked", ""),
                sources=[str(s) for s in item.get("sources", [])],
            ))
        return cls([c for c in companies if c.name])
