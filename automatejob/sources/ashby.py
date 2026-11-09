"""Ashby public job-board API adapter.

Endpoint (public, no auth), per Ashby's official docs:
    https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true

The board name is the last path segment of the company's Ashby careers page
(e.g. ``jobs.ashbyhq.com/kong`` -> ``kong``). One request returns every
published posting with full ``descriptionHtml``/``descriptionPlain`` text.

Fields mapped per Ashby's published schema:
    title, location, secondaryLocations, workplaceType (OnSite/Remote/Hybrid),
    employmentType (FullTime/PartTime/Intern/Contract/Temporary),
    descriptionHtml, publishedAt, jobUrl, applyUrl, isListed.
"""

from __future__ import annotations

from typing import List

from ..html import html_to_text
from ..models import Job
from .base import BaseSource

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
_DESCRIPTION_MAX = 12000

# Ashby's employmentType enum -> a human label we store.
_EMPLOYMENT_MAP = {
    "fulltime": "Full-time",
    "parttime": "Part-time",
    "intern": "Internship",
    "contract": "Contract",
    "temporary": "Temporary",
}


class AshbySource(BaseSource):
    source_type = "ashby"

    def fetch(self) -> List[Job]:
        data = self._get_json(_BOARD_URL.format(token=self.token))
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError(f"unexpected Ashby response shape: {type(data).__name__}")

        jobs: List[Job] = []
        for item in data["jobs"]:
            if item.get("isListed") is False:
                continue  # unlisted/private posting — not on the public board

            title = item.get("title") or ""
            location = item.get("location") or ""
            workplace = (item.get("workplaceType") or "").lower()
            employment_raw = (item.get("employmentType") or "").lower()
            employment = _EMPLOYMENT_MAP.get(employment_raw, item.get("employmentType") or "")

            secondary = [
                s.get("location") or ""
                for s in (item.get("secondaryLocations") or [])
                if s.get("location")
            ]

            description = html_to_text(item.get("descriptionHtml") or "")
            if not description:
                description = (item.get("descriptionPlain") or "")[:_DESCRIPTION_MAX]
            else:
                description = description[:_DESCRIPTION_MAX]

            url = item.get("jobUrl") or item.get("applyUrl") or ""

            jobs.append(Job(
                source_type=self.source_type,
                source_token=self.token,
                external_id=self._external_id(item, url),
                title=title,
                company=self.name or self.token,
                location=location,
                url=url,
                apply_url=item.get("applyUrl") or "",
                workplace_type=workplace,
                employment_type=employment,
                posted_at=item.get("publishedAt") or "",
                description=description,
                secondary_locations=secondary,
            ))
        return jobs

    @staticmethod
    def _external_id(item: dict, url: str) -> str:
        # Ashby has no numeric id on the public endpoint; derive a stable id
        # from the job URL so re-fetches dedupe against themselves.
        if item.get("id"):
            return str(item["id"])
        if url:
            return url.rstrip("/").rsplit("/", 1)[-1]
        return (item.get("title") or "")[:80]
