"""Permitted company-discovery source adapters.

Two verified public interfaces:

* YC company directory  — https://api.ycombinator.com/v0.1/companies
  (public JSON, paginated). Exposes name, website, description, team size,
  batch, tags, industries, regions and locations for every YC company.
* GitHub org API       — https://api.github.com/orgs/{org} (public, rate-limited
  60 req/hr unauthenticated). Used to confirm engineering activity and detect
  technologies from an org's repository languages.

Both are read-only public endpoints; no scraping, no auth, no gated access.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from ..company import (
    Company, CompanySource, REMOTE_INDIA, REMOTE_UNKNOWN, REMOTE_WORLDWIDE,
)

log = logging.getLogger("automatejob")

YC_API = "https://api.ycombinator.com/v0.1/companies"
GITHUB_API = "https://api.github.com"

# Tech keywords worth surfacing from repo languages / org metadata.
_TECH_KEYWORDS = {
    "go": "Go", "golang": "Go", "python": "Python", "kubernetes": "Kubernetes",
    "docker": "Docker", "terraform": "Terraform", "rust": "Rust",
    "typescript": "TypeScript", "javascript": "JavaScript", "react": "React",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mysql": "MySQL",
    "mongodb": "MongoDB", "redis": "Redis", "kafka": "Kafka",
    "aws": "AWS", "azure": "Azure", "gcp": "GCP", "llm": "LLM",
    "machine learning": "ML", "ai": "AI",
}


class CompanySourceError(Exception):
    pass


def _get_json(http, url: str, timeout: float = 20.0, retries: int = 2, delay: float = 2.0):
    """GET a JSON endpoint with retry/backoff, raising CompanySourceError."""
    import requests
    last = None
    for attempt in range(retries + 1):
        try:
            resp = http.get(url, timeout=timeout)
        except Exception as exc:
            last = exc
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 429):
                last = CompanySourceError(f"HTTP {resp.status_code} for {url}")
                time.sleep(delay * (attempt + 1))
                continue
            raise CompanySourceError(f"HTTP {resp.status_code} for {url}")
        time.sleep(delay)
    raise CompanySourceError(f"request failed for {url}: {last!r}")


class YCDirectorySource(CompanySource):
    """Fetch YC companies, paginated (bounded by ``max_pages``)."""

    source_type = "yc_directory"

    def __init__(self, http=None, max_pages: Optional[int] = None, delay: float = 1.0):
        if http is None:
            import requests
            http = requests.Session()
        self._http = http
        self.max_pages = max_pages
        self.delay = delay

    def fetch(self) -> List[Company]:
        out: List[Company] = []
        for batch in self.iter_pages():
            out.extend(batch)
        return out

    def iter_pages(self):
        """Yield one page's companies at a time (resume-safe, bounded)."""
        url = YC_API
        pages = 0
        while url:
            pages += 1
            if self.max_pages and pages > self.max_pages:
                break
            data = _get_json(self._http, url)
            yield [self._map(item) for item in data.get("companies", [])]
            url = data.get("nextPage") or ""
            if url and self.delay:
                time.sleep(self.delay)

    def _map(self, item: dict) -> Company:
        locations = [str(l) for l in (item.get("locations") or [])]
        regions = [str(r) for r in (item.get("regions") or [])]
        tags = [str(t) for t in (item.get("tags") or [])]
        industries = [str(i) for i in (item.get("industries") or [])]

        text = " ".join(locations + regions).lower()
        mumbai = True if any("mumbai" in x.lower() for x in locations + regions) else None
        # Remote eligibility: only claim world-wide/India remote on an explicit
        # signal. Bare "Remote" is ambiguous (often US-only) -> unknown.
        remote = REMOTE_UNKNOWN
        if any("india" in x.lower() for x in locations + regions) and "remote" in text:
            remote = REMOTE_INDIA
        elif "remote" in text and any(w in text for w in ("worldwide", "global", "anywhere")):
            remote = REMOTE_WORLDWIDE

        one_liner = (item.get("oneLiner") or "").strip()
        long_desc = (item.get("longDescription") or "").strip()
        product = one_liner
        if long_desc and long_desc != one_liner:
            product = (one_liner + ". " + long_desc)[:2000]

        team = item.get("teamSize")
        size = "unknown"
        if team is not None:
            size = "1-10" if team <= 10 else ("11-50" if team <= 50 else "51+")

        batch = item.get("batch") or ""
        return Company(
            name=item.get("name") or "",
            website=item.get("website") or "",
            product=product,
            industry=", ".join(tags[:4]) or (", ".join(industries[:4])),
            headquarters=(regions[0] if regions else ""),
            office_locations=locations,
            mumbai_office=mumbai,
            remote_policy=remote,
            company_size=size,
            funding_stage=f"YC {batch}" if batch else "unknown",
            sources=[item.get("url") or ""],
        )


class GitHubOrgSource(CompanySource):
    """Enrich companies from their GitHub org: confirm engineering + technologies."""

    source_type = "github_org"

    def __init__(self, org_slugs: List[str], http=None, delay: float = 0.5):
        if http is None:
            import requests
            http = requests.Session()
        self._http = http
        self.org_slugs = org_slugs
        self.delay = delay

    def fetch(self) -> List[Company]:
        out: List[Company] = []
        for slug in self.org_slugs:
            try:
                org = _get_json(self._http, f"{GITHUB_API}/orgs/{slug}")
                techs = self._org_technologies(slug)
                out.append(Company(
                    name=org.get("name") or org.get("login") or slug,
                    website=org.get("blog") or "",
                    product=(org.get("description") or "").strip(),
                    headquarters=org.get("location") or "",
                    company_size="unknown",
                    technologies=techs,
                    sources=[org.get("html_url") or f"https://github.com/{slug}"],
                ))
            except CompanySourceError as exc:
                log.warning("github org %s: %s", slug, exc)
            if self.delay:
                time.sleep(self.delay)
        return out

    def _org_technologies(self, slug: str) -> List[str]:
        found = []
        try:
            repos = _get_json(self._http, f"{GITHUB_API}/orgs/{slug}/repos?per_page=30&sort=pushed")
        except CompanySourceError:
            return found
        langs = set()
        for repo in repos[:30]:
            lang = repo.get("language")
            if lang:
                langs.add(lang.lower())
            for k, label in _TECH_KEYWORDS.items():
                if k in (repo.get("description") or "").lower() or k in (repo.get("name") or "").lower():
                    found.append(label)
        for lang in sorted(langs):
            for k, label in _TECH_KEYWORDS.items():
                if k == lang:
                    found.append(label)
        return list(dict.fromkeys(found))[:20]
