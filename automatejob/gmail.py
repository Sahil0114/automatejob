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
    def __init__(self, config: GmailConfig, sending_enabled: bool = False):
        self.config = config
        self.sending_enabled = sending_enabled  # False => never send, even if authorized
        self.dry_run = True  # no live Gmail until OAuth is wired and enabled

    def available(self) -> bool:
        return bool(self.config.credentials_file and self.config.token_file)

    @staticmethod
    def draft_idempotency_key(company_id: str, contact_email: str, draft_type: str) -> str:
        return hashlib.sha256(f"{company_id}|{contact_email}|{draft_type}".encode()).hexdigest()

    def save_draft(self, subject: str, body: str, to_addr: str) -> dict:
        """Create a Gmail draft. Returns a status; a draft is NOT a sent email."""
        if not self.available():
            return {"status": "unavailable", "draft_id": "", "error": "Gmail not authorized"}
        if self.dry_run:
            return {"status": "dry_run",
                    "draft_id": f"dry-{hashlib.sha256(body.encode()).hexdigest()[:12]}",
                    "error": ""}
        # Live path (requires authorized OAuth): create a draft via the Gmail
        # API and return its real draft id. Not implemented without credentials.
        return {"status": "unavailable", "draft_id": "", "error": "live draft creation requires OAuth"}

    def send(self, to_addr: str, subject: str, body: str) -> dict:
        """Send a message. Hard-gated by sending_enabled and authorization."""
        if not self.sending_enabled:
            return {"status": "failed", "error": "sending disabled"}
        if not self.available():
            return {"status": "failed", "error": "Gmail not authorized"}
        if self.dry_run:
            return {"status": "confirmation_unknown",
                    "thread_id": "", "message_id": "",
                    "error": "dry-run: nothing sent, reconcile before retry"}
        # Live send via Gmail API returns {status: sent|confirmation_unknown,
        # thread_id, message_id}. Not reachable without OAuth.
        return {"status": "failed", "error": "live send requires OAuth"}

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
