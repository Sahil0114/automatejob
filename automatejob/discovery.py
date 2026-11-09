"""Company discovery orchestrator.

Runs permitted company sources, performs ATS detection (with live validation),
scores each company, links it to any relevant opening, and stores the result.

Bounded by design: YC pagination is capped, ATS probing is opt-in and
rate-limited, and every inferred ATS identifier is validated against the live
endpoint before being recorded.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from .ats_detect import detect_ats
from .company import Company, CompanyStorage
from .company_scoring import relevant_opening_companies, score_company
from .models import normalize_company
from .sources.company_sources import YCDirectorySource

log = logging.getLogger("automatejob")


class CompanyDiscovery:
    def __init__(self, company_storage: CompanyStorage, jobs_storage=None):
        self.storage = company_storage
        self.jobs_storage = jobs_storage

    def discover_yc(self, max_pages: Optional[int] = None, delay: float = 1.0) -> int:
        """Fetch YC companies page-by-page, storing incrementally (resume-safe)."""
        source = YCDirectorySource(max_pages=max_pages, delay=delay)
        total = 0
        for batch in source.iter_pages():
            self.storage.upsert(batch)
            total += len(batch)
        log.info("yc_directory: fetched %d companies", total)
        return total

    def detect_ats_for(self, companies: List[Company], limit: Optional[int] = None,
                       delay: float = 1.0) -> int:
        """Detect + validate ATS for companies (bounded, rate-limited)."""
        import requests
        http = requests.Session()
        done = 0
        for c in companies:
            if limit is not None and done >= limit:
                break
            if c.ats_type:  # already known
                continue
            ats, token, url = detect_ats(c.name, c.domain, c.careers_url, http=http, probe=True)
            if ats:
                c.ats_type, c.ats_token, c.careers_url = ats, token, url
                c.sources.append(url)
                done += 1
            if delay:
                time.sleep(delay)
        if done:
            self.storage.upsert(companies)
        return done

    def score_all(self) -> int:
        """Re-score every stored company against the current jobs table."""
        companies = [self._row_to_company(r) for r in self.storage.conn.execute(
            "SELECT * FROM companies").fetchall()]
        names = [c.name for c in companies]
        with_openings = relevant_opening_companies(self.jobs_storage, names) if self.jobs_storage else set()
        updated = 0
        for c in companies:
            has = normalize_company(c.name) in with_openings
            score_company(c, has_relevant_opening=has)
            self.storage.upsert([c])
            updated += 1
        return updated

    @staticmethod
    def _row_to_company(r) -> Company:
        import json as _json
        return Company(
            name=r["name"], website=r["website"], careers_url=r["careers_url"],
            ats_type=r["ats_type"], ats_token=r["ats_token"],
            industry=r["industry"], product=r["product"],
            headquarters=r["headquarters"],
            office_locations=_json.loads(r["office_locations"] or "[]"),
            mumbai_office=r["mumbai_office"],
            mumbai_engineering=r["mumbai_engineering"],
            remote_policy=r["remote_policy"], company_size=r["company_size"],
            funding_stage=r["funding_stage"],
            technologies=_json.loads(r["technologies"] or "[]"),
            sources=_json.loads(r["sources"] or "[]"),
            last_verified=r["last_verified"], category=r["category"],
            score=r["score"], score_reasons=_json.loads(r["score_reasons"] or "[]"),
        )
