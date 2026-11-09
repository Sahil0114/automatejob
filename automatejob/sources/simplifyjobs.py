"""SimplifyJobs internship / new-grad list collector.

Reads the public ``README.md`` tables from:
  * https://github.com/SimplifyJobs/Summer2027-Internships
  * https://github.com/SimplifyJobs/New-Grad-Positions

Each HTML table row is ``Company | Role | Location | Application | Age``.
The employer's ORIGINAL application link is retained; the Simplify wrapper
links are dropped. US/Canada-only locations are left for the eligibility
classifier to exclude — this adapter does NOT assume they are open to India.
"""

from __future__ import annotations

import html
import re
from typing import List

from ..models import Job
from .base import BaseSource, SourceError

_RAW_URL = "https://raw.githubusercontent.com/SimplifyJobs/{token}/dev/README.md"

_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")

# US city abbreviations used in the SimplifyJobs tables, expanded so the
# eligibility classifier recognises them as foreign (excluded) locations.
_LOC_ABBREV = {
    "nyc": "New York", "sf": "San Francisco", "sfo": "San Francisco",
    "la": "Los Angeles", "dc": "Washington DC", "sfnyc": "San Francisco / New York",
    "sea": "Seattle", "chi": "Chicago", "bos": "Boston", "atx": "Austin",
    "remote": "Remote",
}


def _strip_tags(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _expand_location(location: str) -> str:
    parts = [p.strip() for p in location.split(",") if p.strip()]
    out = []
    for p in parts:
        low = p.lower()
        out.append(_LOC_ABBREV.get(low, p))
    return ", ".join(out)


class SimplifyJobsSource(BaseSource):
    source_type = "simplifyjobs"

    def fetch(self) -> List[Job]:
        url = _RAW_URL.format(token=self.token)
        try:
            resp = self._http.get(url, timeout=self.timeout)
        except Exception as exc:
            raise SourceError(f"request failed for {url}: {exc!r}") from exc
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} for {url}")
        text = resp.text

        is_internship = "intern" in self.token.lower()
        jobs: List[Job] = []
        prev_company = ""
        for row in _ROW_RE.findall(text):
            cells = _TD_RE.findall(row)
            if len(cells) < 4:
                continue
            company = _strip_tags(cells[0])
            if not company or company in ("↳", "Company"):
                if company == "Company":
                    continue
                company = prev_company  # "↳" sub-role inherits company
            if not company:
                continue
            prev_company = company
            role = _strip_tags(cells[1])
            if not role or role == "Role":
                continue
            location_html = cells[2].replace("<br>", ", ").replace("<br/>", ", ")
            location = _expand_location(_strip_tags(location_html))
            location = re.sub(r"\s*,\s*", ", ", location).strip(" ,")
            if not location:
                location = "Remote"

            # first non-Simplify href in the application cell = employer link
            apply_url = ""
            for href in _HREF_RE.findall(cells[3]):
                if "simplify.jobs" not in href and "imgur" not in href:
                    apply_url = href
                    break

            workplace = "remote" if "remote" in location.lower() else ""
            if "hybrid" in location.lower():
                workplace = "hybrid"

            jobs.append(Job(
                source_type=self.source_type,
                source_token=self.token,
                external_id=apply_url or f"{company}|{role}|{location}",
                title=role,
                company=company,
                location=location,
                url=apply_url or "",
                apply_url=apply_url,
                workplace_type=workplace,
                employment_type="Internship" if is_internship else "Full-time",
                posted_at="",
                description=f"{company} — {role}. Location: {location}.",
            ))
        if not jobs:
            raise SourceError(f"no rows parsed from {self.token}")
        return jobs
