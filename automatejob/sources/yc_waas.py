"""YC Work-at-a-Startup job board adapter.

YC's official job board (workatastartup.com) server-renders each company page
with the full job list embedded as JSON in a ``data-page`` attribute
(``props.company.jobs``). Each job carries structured fields — title, location,
jobType, roleType, salary, ``minExperience`` and ``sponsorsVisa`` — which we map
onto the standard ``Job`` model so the existing eligibility / seniority gates
apply unchanged.

Fetching is per-company and incremental: a source token is a comma-separated
list of YC company slugs (or a single slug), so unchanged companies are not
refetched unnecessarily. This is the source that previously surfaced only the
generic HN MixRank posting and missed the specific "Junior Software Engineer -
Remote (Global)" opening.
"""

from __future__ import annotations

import html as _html
import json
import re
from typing import List, Optional

from ..models import Job
from .base import BaseSource, SourceError

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "automatejob/1.0 (job search)"}
_COMPANY_URL = "https://www.workatastartup.com/companies/{slug}"

_DATA_PAGE_RE = re.compile(r'data-page="([^"]*)"')


def _extract_jobs(page_html: str) -> tuple[dict, List[dict]]:
    """Return (company_dict, jobs_list) from a workatastartup company page."""
    m = _DATA_PAGE_RE.search(page_html)
    if not m:
        raise SourceError("no data-page JSON found on workatastartup page")
    try:
        data = json.loads(_html.unescape(m.group(1)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise SourceError(f"invalid data-page JSON: {exc}") from exc
    company = data.get("props", {}).get("company", {})
    if not company:
        raise SourceError("no company data in workatastartup page")
    jobs = company.get("jobs") or []
    return company, jobs


class YCJobsSource(BaseSource):
    source_type = "yc_waas"

    def __init__(self, token: str, name: str = "", http=None, timeout: float = 30.0):
        super().__init__(token, name, http, timeout)
        if self._http is not None and hasattr(self._http, "headers"):
            self._http.headers.update(_UA)

    @property
    def slugs(self) -> List[str]:
        return [s.strip() for s in self.token.split(",") if s.strip()]

    def _get_html(self, url: str) -> str:
        try:
            resp = self._http.get(url, headers=_UA, timeout=self.timeout)
        except Exception as exc:
            raise SourceError(f"request failed for {url}: {exc!r}") from exc
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} for {url}")
        return resp.text

    def fetch(self) -> List[Job]:
        jobs: List[Job] = []
        for slug in self.slugs:
            url = _COMPANY_URL.format(slug=slug)
            try:
                company, company_jobs = _extract_jobs(self._get_html(url))
            except SourceError as exc:
                raise SourceError(f"{slug}: {exc}") from exc
            jobs.extend(self._map_company_jobs(company, company_jobs))
        return jobs

    def _map_company_jobs(self, company: dict, company_jobs: List[dict]) -> List[Job]:
        name = company.get("name") or self.name or ""
        one_liner = (company.get("description") or "").strip()
        out: List[Job] = []
        for j in company_jobs:
            title = j.get("title") or ""
            location = j.get("location") or ""
            min_exp = (j.get("minExperience") or "").strip()
            visa = (j.get("sponsorsVisa") or "").strip()

            # "Remote (Global)" / "worldwide" often lives in the TITLE while the
            # location field is just "Remote"; surface it so the eligibility
            # classifier recognises the worldwide-remote signal.
            tl = title.lower()
            if ("global" in tl or "worldwide" in tl or "anywhere" in tl) \
                    and "global" not in location.lower() \
                    and "worldwide" not in location.lower():
                location = f"{location} (Global)".strip(" ()")

            workplace = "remote" if "remote" in (location + " " + title).lower() else ""
            if "hybrid" in (location + " " + title).lower():
                workplace = "hybrid"

            # Fold structured signals into the searchable description so the
            # existing seniority / role-family classifiers see them.
            desc_parts = [p for p in [title, one_liner, j.get("roleType") or "",
                                      j.get("jobType") or "", min_exp, visa,
                                      j.get("salary") or ""] if p]
            description = " | ".join(dict.fromkeys(desc_parts))[:12000]

            external_id = str(j.get("id") or "")
            job_url = f"https://www.workatastartup.com/jobs/{external_id}"
            out.append(Job(
                source_type=self.source_type,
                source_token=self.token,
                external_id=external_id,
                title=title,
                company=name,
                location=location,
                url=job_url,
                apply_url=j.get("applyUrl") or job_url,
                workplace_type=workplace,
                employment_type=j.get("jobType") or "",
                posted_at=j.get("companyLastActiveAt") or "",
                description=description,
            ))
        return out
