"""Hacker News "Who is hiring?" collector (public Algolia API).

Fetches the latest "Ask HN: Who is hiring?" story via the HN Algolia API, then
walks its top-level comments (each comment is one job posting). The first line
of each comment follows the convention
``Company | Role | Location | REMOTE(...)/ONSITE/HYBRID | extras``.

Only the current month's thread is fetched (bounded); ambiguous remote/location
eligibility is left for the eligibility classifier to route to needs_verification.
"""

from __future__ import annotations

import html
from typing import List

from ..html import html_to_text
from ..models import Job
from .base import BaseSource, SourceError

_SEARCH_URL = ('https://hn.algolia.com/api/v1/search_by_date'
               '?query=%22who%20is%20hiring%22&tags=story&hitsPerPage=5')
_ITEM_URL = "https://hn.algolia.com/api/v1/items/{object_id}"
_HN_URL = "https://news.ycombinator.com/item?id={comment_id}"


def _first_line(text: str) -> str:
    for sep in ("\n", "<p>"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def _detect_workplace(text: str) -> str:
    t = text.lower()
    if "remote" in t:
        return "remote"
    if "hybrid" in t:
        return "hybrid"
    if "onsite" in t or "on-site" in t or "in person" in t or "in-person" in t:
        return "onsite"
    return ""


def _detect_employment(text: str) -> str:
    t = text.lower()
    if "intern" in t:
        return "Internship"
    if "full-time" in t or "full time" in t or "fulltime" in t:
        return "Full-time"
    if "contract" in t:
        return "Contract"
    if "part-time" in t or "part time" in t:
        return "Part-time"
    return ""


class HNSource(BaseSource):
    source_type = "hn"

    def fetch(self) -> List[Job]:
        search = self._get_json(_SEARCH_URL)
        stories = [h for h in search.get("hits", [])
                   if "hiring" in (h.get("title") or "").lower()]
        if not stories:
            raise SourceError("no 'Who is hiring?' story found")
        object_id = stories[0]["objectID"]
        item = self._get_json(_ITEM_URL.format(object_id=object_id))

        jobs: List[Job] = []
        for comment in item.get("children", []):
            job = self._parse_comment(comment)
            if job is not None:
                jobs.append(job)
        return jobs

    def _parse_comment(self, comment: dict) -> Job | None:
        raw = comment.get("text") or ""
        text = html.unescape(html_to_text(raw)).strip()
        if not text:
            return None
        header = _first_line(text)
        fields = [f.strip() for f in header.split("|")]
        if len(fields) < 2:
            return None

        company = fields[0]
        title = fields[1]
        # location + any remote/onsite markers merged so the eligibility
        # classifier sees the full signal (e.g. "REMOTE (worldwide, any timezone)").
        location = " ".join(fields[2:]) if len(fields) > 2 else ""
        workplace = _detect_workplace(location) or _detect_workplace(text)
        employment = _detect_employment(header)

        return Job(
            source_type=self.source_type,
            source_token=self.token,
            external_id=str(comment.get("id") or ""),
            title=title,
            company=company,
            location=location,
            url=_HN_URL.format(comment_id=comment.get("id") or ""),
            apply_url="",
            workplace_type=workplace,
            employment_type=employment,
            posted_at=comment.get("created_at") or "",
            description=text[:12000],
        )
