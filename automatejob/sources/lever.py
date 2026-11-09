"""Lever public postings API adapter.

Endpoint (public, no auth):
    https://api.lever.co/v0/postings/{company}?mode=json

Lever includes a plain-text description for each posting, which is used
(truncated) for keyword-based role matching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from ..models import Job
from .base import BaseSource

_POSTINGS_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
_DESCRIPTION_MAX = 2000


def _ms_to_iso(ms: int) -> str:
    """Convert a millisecond epoch to ISO-8601 UTC, or '' when unknown."""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


class LeverSource(BaseSource):
    source_type = "lever"

    def fetch(self) -> List[Job]:
        data = self._get_json(_POSTINGS_URL.format(token=self.token))
        if not isinstance(data, list):
            raise ValueError(f"unexpected Lever response shape: {type(data).__name__}")

        jobs: List[Job] = []
        for item in data:
            cats = item.get("categories") or {}
            title = item.get("text") or ""
            location = cats.get("location") or ""
            description = (item.get("descriptionPlain") or "")[:_DESCRIPTION_MAX]

            jobs.append(Job(
                source_type=self.source_type,
                source_token=self.token,
                external_id=str(item.get("id") or ""),
                title=title,
                company=self.name or self.token,
                location=location,
                url=item.get("hostedUrl") or "",
                apply_url=item.get("applyUrl") or "",
                workplace_type=(item.get("workplaceType") or "").lower(),
                employment_type=cats.get("commitment") or "",
                posted_at=_ms_to_iso(item.get("createdAt")),
                description=description,
            ))
        return jobs
