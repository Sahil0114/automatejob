"""We Work Remotely RSS adapter (public, permitted feed).

We Work Remotely publishes public RSS feeds per category, e.g.:

    https://weworkremotely.com/categories/remote-programming-jobs.rss
    https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss

This adapter parses the RSS with the standard library (no third-party
dependency), normalises each item into the standard ``Job`` model, and leaves
remote/India eligibility classification to the existing eligibility gates
(which conservatively route ambiguous remote roles to needs_verification).
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import List

from ..html import html_to_text
from ..models import Job
from .base import BaseSource, SourceError

_FEED_URL = "https://weworkremotely.com/categories/{category}.rss"

_CATEGORY_ALIASES = {
    "programming": "remote-programming-jobs",
    "devops": "remote-devops-sysadmin-jobs",
    "devops-sysadmin": "remote-devops-sysadmin-jobs",
    "remote-programming-jobs": "remote-programming-jobs",
    "remote-devops-sysadmin-jobs": "remote-devops-sysadmin-jobs",
}

_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


class WeWorkRemotelySource(BaseSource):
    source_type = "weworkremotely"

    def __init__(self, token: str, name: str = "", http=None, timeout: float = 30.0):
        super().__init__(token, name, http, timeout)
        self.category = _CATEGORY_ALIASES.get(token.strip().lower(), token.strip())

    @property
    def feed_url(self) -> str:
        return _FEED_URL.format(category=self.category)

    def _get_xml(self, url: str) -> str:
        try:
            resp = self._http.get(url, timeout=self.timeout)
        except Exception as exc:
            raise SourceError(f"request failed for {url}: {exc!r}") from exc
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} for {url}")
        return resp.text

    def fetch(self) -> List[Job]:
        try:
            root = ET.fromstring(self._get_xml(self.feed_url))
        except ET.ParseError as exc:
            raise SourceError(f"invalid RSS XML from {self.feed_url}: {exc}") from exc

        jobs: List[Job] = []
        for item in root.findall(".//item"):
            job = self._parse_item(item)
            if job is not None:
                jobs.append(job)
        return jobs

    def _parse_item(self, item: ET.Element) -> Job | None:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            return None

        description = (item.findtext("description") or "").strip()
        description = html.unescape(html_to_text(description)).strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        company, role = self._split_title(title)
        workplace = self._detect_workplace(title + " " + description)

        return Job(
            source_type=self.source_type,
            source_token=self.token,
            external_id=link,
            title=role or title,
            company=company or self.name or "We Work Remotely",
            location="Remote",
            url=link,
            apply_url=link,
            workplace_type=workplace,
            employment_type="Full-time",
            posted_at=self._parse_pub_date(pub_date),
            description=description[:12000],
        )

    @staticmethod
    def _split_title(title: str) -> tuple[str, str]:
        """WWR titles are often ``Company: Role`` or ``Role at Company``."""
        if ": " in title:
            company, _, role = title.partition(": ")
            return company.strip(), role.strip()
        if " at " in title:
            role, _, company = title.partition(" at ")
            return company.strip(), role.strip()
        return "", title.strip()

    @staticmethod
    def _detect_workplace(text: str) -> str:
        t = text.lower()
        if "remote" in t:
            return "remote"
        if "hybrid" in t:
            return "hybrid"
        return ""

    @staticmethod
    def _parse_pub_date(value: str) -> str:
        """Best-effort RFC-822 -> ISO-8601; returns '' when unparseable."""
        value = (value or "").strip()
        if not value:
            return ""
        # Strip the trailing weekday and timezone quirks; email.utils handles RFC 822.
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(value)
            return dt.isoformat()
        except (TypeError, ValueError):
            return ""
