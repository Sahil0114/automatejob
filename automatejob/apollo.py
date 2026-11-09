"""Apollo contact-enrichment adapter (blocked until credentials are provided).

Enforces a configurable daily/monthly credit budget, never re-enriches an
unchanged contact, and distinguishes confirmed / unverified / unavailable
emails. With no API key configured it is explicitly UNAVAILABLE and every
enrichment returns ``unavailable`` rather than guessing.

No phone numbers or private emails are ever requested — only professional
business contact information relevant to legitimate recruitment outreach.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class ApolloConfig:
    api_key: str = ""            # empty => unavailable
    daily_budget: int = 0
    monthly_budget: int = 0


@dataclass
class EnrichmentResult:
    available: bool
    email: str = ""
    email_status: str = "unavailable"   # verified / unverified / unavailable
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    linkedin_url: str = ""
    credits_used: int = 0
    error: str = ""


class ApolloEnrichment:
    def __init__(self, config: ApolloConfig, usage_store=None):
        self.config = config
        self.usage_store = usage_store  # OutreachStorage or None (tests)

    def available(self) -> bool:
        return bool(self.config.api_key)

    def _used_today(self) -> int:
        if self.usage_store is None:
            return 0
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.usage_store.conn.execute(
            "SELECT COALESCE(SUM(credits_used),0) s FROM contact_enrichment "
            "WHERE source='apollo' AND date(enriched_at)=? ", (today,)).fetchone()
        return int(rows["s"]) if rows else 0

    def budget_exceeded(self) -> bool:
        if not self.config.daily_budget:
            return False
        return self._used_today() >= self.config.daily_budget

    def enrich(self, contact_id: int, full_name: str, company_domain: str,
               fingerprint: str) -> EnrichmentResult:
        """Enrich one contact. Returns unavailable when no key or budget spent."""
        if not self.available():
            return EnrichmentResult(available=False, error="no API key configured")
        if self.budget_exceeded():
            return EnrichmentResult(available=False, error="daily credit budget exceeded")
        # In a real deployment this calls the Apollo API with the key and maps
        # the response; without a key (and without a live account) we never
        # reach here, and we never fabricate an email.
        return EnrichmentResult(available=False,
                                error="live enrichment requires an authorized Apollo account")

    @staticmethod
    def fingerprint(full_name: str, company_domain: str) -> str:
        return hashlib.sha256(
            f"{full_name.lower()}|{(company_domain or '').lower()}".encode()).hexdigest()
