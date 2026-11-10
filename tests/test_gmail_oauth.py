import base64
import unittest

from automatejob.gmail import GmailAdapter, GmailConfig


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Drafts:
    def __init__(self):
        self.calls = []

    def create(self, userId, body):
        self.calls.append((userId, body))
        return _Request({"id": "draft-123", "message": {"id": "msg-123", "threadId": "thread-123"}})


class _Users:
    def __init__(self, drafts):
        self._drafts = drafts

    def drafts(self):
        return self._drafts


class _Service:
    def __init__(self):
        self.drafts_api = _Drafts()

    def users(self):
        return _Users(self.drafts_api)


class TestGmailDrafts(unittest.TestCase):
    def test_authorized_injected_service_creates_real_draft_payload(self):
        service = _Service()
        adapter = GmailAdapter(GmailConfig(), service=service)

        result = adapter.save_draft("Hello", "Body text", "person@example.com")

        self.assertEqual(result["status"], "gmail_draft")
        self.assertEqual(result["draft_id"], "draft-123")
        self.assertEqual(result["message_id"], "msg-123")
        self.assertEqual(result["thread_id"], "thread-123")
        self.assertEqual(len(service.drafts_api.calls), 1)
        raw = service.drafts_api.calls[0][1]["message"]["raw"]
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        self.assertIn("To: person@example.com", decoded)
        self.assertIn("Subject: Hello", decoded)
        self.assertIn("Body text", decoded)

    def test_draft_creation_failure_is_reported_without_send(self):
        class BadDrafts:
            def create(self, **kwargs):
                raise RuntimeError("oauth revoked")

        class BadService:
            def users(self):
                class Users:
                    def drafts(self):
                        return BadDrafts()
                return Users()

        adapter = GmailAdapter(GmailConfig(), service=BadService())
        result = adapter.save_draft("Hi", "Body", "person@example.com")
        self.assertEqual(result["status"], "failed")
        self.assertIn("oauth revoked", result["error"])

    def test_sending_stays_disabled_even_with_authorized_service(self):
        adapter = GmailAdapter(GmailConfig(), service=_Service(), sending_enabled=False)
        result = adapter.send("person@example.com", "Hi", "Body")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "sending disabled")


if __name__ == "__main__":
    unittest.main()
