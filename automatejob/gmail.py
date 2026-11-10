"""Standalone Gmail OAuth adapter.

The adapter follows Google's installed-desktop OAuth flow. It supports an
explicit local authorization step, account verification, Gmail draft creation,
thread lookup, and read-only reply detection. Sending remains disabled unless
both the caller and adapter explicitly enable it; the dashboard never enables
it by default.

Credential and token files are local-only, git-ignored, and written mode 0600.
No access/refresh token is logged or returned from this module.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

# Minimal scopes for this application's approved capabilities:
# compose = drafts (and potential explicit sends, still hard-gated in code)
# readonly = profile/thread/reply/rejection/bounce detection.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
)


@dataclass
class GmailConfig:
    credentials_file: str = ""  # downloaded OAuth desktop-client JSON
    token_file: str = ""        # local refresh/access token JSON (git-ignored)
    scopes: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SCOPES)


class GmailAdapter:
    def __init__(self, config: GmailConfig, sending_enabled: bool = False, service=None):
        self.config = config
        self.sending_enabled = sending_enabled  # False => never send, even if authorized
        self._service = service                 # injectable for tests / authorized live client

    def available(self) -> bool:
        """Whether an authorized service or configured local OAuth files exist.

        Presence of files is not claimed as successful authorization; live calls
        still return an explicit error when the token is revoked/invalid.
        """
        return bool(self._service is not None or
                    (self.config.credentials_file and self.config.token_file and
                     Path(self.config.credentials_file).exists() and
                     Path(self.config.token_file).exists()))

    @staticmethod
    def draft_idempotency_key(company_id: str, contact_email: str, draft_type: str) -> str:
        return hashlib.sha256(f"{company_id}|{contact_email}|{draft_type}".encode()).hexdigest()

    def _build_service(self):
        """Load/refresh local OAuth credentials and construct a Gmail API client."""
        if self._service is not None:
            return self._service
        if not self.available():
            raise RuntimeError("Gmail OAuth is not configured")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("Gmail client libraries are unavailable") from exc

        creds = Credentials.from_authorized_user_file(self.config.token_file, self.config.scopes)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._write_token(creds.to_json())
            else:
                raise RuntimeError("Gmail OAuth token is invalid or revoked; run gmail-auth")
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _write_token(self, content: str) -> None:
        path = Path(self.config.token_file)
        if not path:
            raise RuntimeError("token file path is not configured")
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL is not appropriate because refreshes overwrite an existing
        # token. chmod after write keeps the persisted secret owner-readable.
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)

    def authorize_interactive(self, port: int = 0) -> dict:
        """Run local browser OAuth for a downloaded Desktop client JSON.

        This intentionally requires an explicit CLI invocation. It opens the
        browser consent flow and writes a local 0600 token file; it never runs
        as part of dashboard startup or collection.
        """
        if not self.config.credentials_file or not Path(self.config.credentials_file).is_file():
            return {"ok": False, "error": "OAuth client credentials file not found"}
        if not self.config.token_file:
            return {"ok": False, "error": "OAuth token file path not configured"}
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            return {"ok": False, "error": "google-auth-oauthlib is not installed"}
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                self.config.credentials_file, self.config.scopes)
            creds = flow.run_local_server(port=port)
            self._write_token(creds.to_json())
            self._service = None
            profile = self.account_profile()
            return {"ok": True, "email": profile.get("email", "")}
        except Exception as exc:
            return {"ok": False, "error": f"OAuth authorization failed: {exc}"}

    def account_profile(self) -> dict:
        """Return the authorized Gmail account address without exposing tokens."""
        try:
            result = self._build_service().users().getProfile(userId="me").execute()
            return {"email": result.get("emailAddress", ""),
                    "messages_total": result.get("messagesTotal", 0),
                    "threads_total": result.get("threadsTotal", 0)}
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _raw_message(to_addr: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    def save_draft(self, subject: str, body: str, to_addr: str, thread_id: str = "") -> dict:
        """Create a real Gmail draft when OAuth is authorized.

        A Gmail draft is never treated as a sent message. Failures are explicit;
        the caller owns local idempotency and must persist the returned draft id.
        """
        if not self.available():
            return {"status": "unavailable", "draft_id": "", "error": "Gmail not authorized"}
        if not to_addr:
            return {"status": "failed", "draft_id": "", "error": "recipient is required"}
        try:
            message = {"raw": self._raw_message(to_addr, subject, body)}
            if thread_id:
                message["threadId"] = thread_id
            result = self._build_service().users().drafts().create(
                userId="me", body={"message": message}).execute()
            returned = result.get("message") or {}
            return {
                "status": "gmail_draft",
                "draft_id": result.get("id", ""),
                "message_id": returned.get("id", ""),
                "thread_id": returned.get("threadId", thread_id),
                "error": "",
            }
        except Exception as exc:
            return {"status": "failed", "draft_id": "", "error": str(exc)}

    def send(self, to_addr: str, subject: str, body: str, thread_id: str = "") -> dict:
        """Explicit send path, disabled by default and never used automatically."""
        if not self.sending_enabled:
            return {"status": "failed", "error": "sending disabled"}
        if not self.available():
            return {"status": "failed", "error": "Gmail not authorized"}
        if not to_addr:
            return {"status": "failed", "error": "recipient is required"}
        try:
            message = {"raw": self._raw_message(to_addr, subject, body)}
            if thread_id:
                message["threadId"] = thread_id
            result = self._build_service().users().messages().send(
                userId="me", body=message).execute()
            return {"status": "sent", "thread_id": result.get("threadId", ""),
                    "message_id": result.get("id", ""), "error": ""}
        except Exception as exc:
            # Do not retry automatically: Gmail may have accepted the message
            # even when a transport response was lost.
            return {"status": "confirmation_unknown", "thread_id": "",
                    "message_id": "", "error": str(exc)}

    def list_threads(self, query: str = "") -> List[dict]:
        if not self.available():
            return []
        try:
            result = self._build_service().users().threads().list(
                userId="me", q=query, maxResults=100).execute()
            return result.get("threads") or []
        except Exception:
            return []

    def thread_has_reply(self, thread_id: str) -> Optional[bool]:
        """True only when a received message is identifiable in the thread.

        Returns None when OAuth/API state is unavailable — callers must then
        pause follow-up rather than assuming no reply.
        """
        if not self.available() or not thread_id:
            return None
        try:
            profile = self.account_profile()
            own_email = (profile.get("email") or "").lower()
            thread = self._build_service().users().threads().get(
                userId="me", id=thread_id, format="metadata",
                metadataHeaders=["From"]).execute()
            for message in thread.get("messages") or []:
                headers = {h.get("name", "").lower(): h.get("value", "")
                           for h in (message.get("payload", {}).get("headers") or [])}
                sender = headers.get("from", "").lower()
                if sender and own_email and own_email not in sender:
                    return True
            return False
        except Exception:
            return None

    def sync_sent(self, since: str = "") -> List[dict]:
        """Return sent-message metadata since an optional YYYY/MM/DD date."""
        if not self.available():
            return []
        query = "in:sent"
        if since:
            query += f" after:{since.replace('-', '/')}"
        try:
            result = self._build_service().users().messages().list(
                userId="me", q=query, maxResults=100).execute()
            return result.get("messages") or []
        except Exception:
            return []
