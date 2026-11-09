"""Automatic ATS detection with live validation.

Given a company name/domain/careers URL, detect whether it uses Greenhouse,
Lever or Ashby and recover its verified board identifier. Inferred tokens are
NEVER trusted on their own: every candidate is validated against the public
board endpoint (200 = verified, 404 = not this ATS).

Only public, documented, read-only endpoints are probed. Rate-limited: one
HEAD-style GET per candidate per ATS, with a bounded number of candidates.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

# ATS -> (careers-url regex, public validation endpoint template)
_ATS = [
    ("greenhouse", re.compile(r"boards(?:-api|-eu)?\.greenhouse\.io/([a-z0-9\-_]+)"),
     "https://boards-api.greenhouse.io/v1/boards/{t}/jobs"),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9\-_]+)"),
     "https://api.lever.co/v0/postings/{t}?mode=json"),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9\-_]+)"),
     "https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true"),
]


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s


def _candidates(name: str, domain: str) -> List[str]:
    """Bounded set of candidate board tokens to probe."""
    cands = []
    d = (domain or "").split(".")[0]
    if d:
        cands.append(d)
    slug = _slugify(name)
    if slug and slug not in cands:
        cands.append(slug)
    # strip common suffixes
    for suffix in ("-inc", "-technologies", "-labs", "-hq"):
        if slug.endswith(suffix) and slug[: -len(suffix)]:
            cands.append(slug[: -len(suffix)])
    return cands[:6]


def _probe(http, url: str, timeout: float = 12.0) -> bool:
    try:
        resp = http.get(url, timeout=timeout, stream=True)
        resp.close()
        return resp.status_code == 200
    except Exception:
        return False


def detect_ats(name: str, domain: str, careers_url: str = "",
               http=None, probe: bool = True,
               timeout: float = 12.0) -> Tuple[str, str, str]:
    """Return (ats_type, token, evidence_url), or ("", "", "") if unverified."""
    if http is None:
        import requests
        http = requests.Session()

    # 1. Exact ATS pattern in the careers URL (highest-confidence).
    if careers_url:
        for ats, pat, endpoint in _ATS:
            m = pat.search(careers_url or "")
            if m:
                token = m.group(1)
                url = endpoint.format(t=token)
                if _probe(http, url, timeout):
                    return ats, token, url

    # 2. Probe candidate tokens against each ATS (validated, never trusted).
    if probe:
        for token in _candidates(name, domain):
            for ats, _, endpoint in _ATS:
                url = endpoint.format(t=token)
                if _probe(http, url, timeout):
                    return ats, token, url
    return "", "", ""
