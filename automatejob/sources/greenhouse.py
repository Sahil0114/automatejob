"""Greenhouse public board API adapter.

Endpoint (public, no auth):
    https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

``content=true`` includes each post's full description (HTML in the
``content`` field) plus its departments and offices. The HTML is converted to
searchable plain text with :func:`automatejob.html.html_to_text` so
description-based role/seniority matching works for Greenhouse too.
"""

from __future__ import annotations

from typing import List

from ..html import html_to_text
from ..models import Job
from .base import BaseSource

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
_DESCRIPTION_MAX = 12000


class GreenhouseSource(BaseSource):
    source_type = "greenhouse"

    def fetch(self) -> List[Job]:
        data = self._get_json(_BOARD_URL.format(token=self.token))
        jobs: List[Job] = []
        for item in data.get("jobs", []):
            location = (item.get("location") or {}).get("name") or ""
            title = item.get("title") or ""
            company = item.get("company_name") or self.name or self.token
            workplace = "remote" if "remote" in location.lower() else ""
            if "hybrid" in location.lower():
                workplace = "hybrid"

            description = html_to_text(item.get("content") or "")[:_DESCRIPTION_MAX]

            employment = ""
            for m in item.get("metadata") or []:
                name = (m.get("name") or "").lower()
                if "employment" in name or name in ("commitment",):
                    employment = m.get("value") or ""
                    break

            jobs.append(Job(
                source_type=self.source_type,
                source_token=self.token,
                external_id=str(item.get("id") or ""),
                title=title,
                company=company,
                location=location,
                url=item.get("absolute_url") or "",
                apply_url=item.get("absolute_url") or "",
                workplace_type=workplace,
                employment_type=employment,
                posted_at=item.get("first_published") or "",
                description=description,
            ))
        return jobs
