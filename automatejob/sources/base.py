"""Base class for job source adapters."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import List

from ..models import Job


class SourceError(Exception):
    """Raised when a source cannot be fetched or parsed."""


class BaseSource(ABC):
    """Common plumbing shared by all adapters: HTTP session, JSON fetch."""

    source_type: str = ""

    def __init__(self, token: str, name: str = "", http=None, timeout: float = 30.0):
        self.token = token
        self.name = name or token
        self.timeout = timeout
        # `http` is injected so tests can pass a fake; defaults to requests.
        if http is None:
            import requests  # local import keeps requests optional for tests
            self._http = requests.Session()
        else:
            self._http = http

    def display_company(self, fallback: str = "") -> str:
        """Company name to display (source-configured name wins, else fallback)."""
        return self.name or fallback or self.token

    def _get_json(self, url: str, headers: dict | None = None):
        """GET a JSON endpoint and return the parsed body, raising SourceError."""
        try:
            resp = self._http.get(url, headers=headers, timeout=self.timeout)
        except Exception as exc:  # network / TLS / timeout errors
            raise SourceError(f"request failed for {url}: {exc!r}") from exc
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} for {url}")
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceError(f"invalid JSON from {url}: {exc}") from exc

    @abstractmethod
    def fetch(self) -> List[Job]:
        """Fetch and normalise all current postings for this source."""
        raise NotImplementedError
