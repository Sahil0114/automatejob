"""Gmail integration adapter (dry-run only; blocked until OAuth is authorized).

Supports: draft creation (idempotent), thread lookup, reply/bounce/rejection
detection, sent-message sync. With no OAuth credentials configured the adapter
is UNAVAILABLE and operates in dry-run mode: no network call, no message is
ever created or sent. The final application must never treat a dry-run draft
as sent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class GmailConfig:
    credentials_file: str = ""   # path to OAuth client secret (absent => unavailable)
    token_file: str = ""         # path to stored user token


class GmailAdapter:
    def __init__(self, config: GmailConfig):
        self.config = config
        self.dry_run = True  # never send during this phase

    def available(self) -> bool:
        return bool(self.config.credentials_file and self.config.token_file)

    @staticmethod
    def draft_idempotency_key(company_id: str, contact_email: str, draft_type: str) -> str:
        return hashlib.sha256(f"{company_id}|{contact_email}|{draft_type}".encode()).hexdigest()

    def create_draft(self, subject: str, body: str, to_addr: str) -> dict:
        """Create a draft. Dry-run: returns a placeholder and NEVER writes to Gmail."""
        if not self.available():
            return {"status": "unavailable", "draft_id": "", "error": "Gmail not authorized"}
        if self.dry_run:
            return {"status": "dry_run", "draft_id": f"dry-{hashlib.sha256(body.encode()).hexdigest()[:12]}",
                    "error": ""}
        return {"status": "unavailable", "draft_id": "", "error": "live send disabled in this phase"}

    def list_threads(self, query: str = "") -> List[dict]:
        if not self.available():
            return []
        return []  # dry-run: no live fetch

    def thread_has_reply(self, thread_id: str) -> Optional[bool]:
        if not self.available():
            return None
        return None  # dry-run: unknown — caller must check mailbox state

    def sync_sent(self, since: str = "") -> List[dict]:
        if not self.available():
            return []
        return []
