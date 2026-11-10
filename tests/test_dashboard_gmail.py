import io
import json
import unittest

from automatejob.dashboard import Dashboard
from automatejob.outreach import OutreachStorage


class _W:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b


class _Handler:
    def __init__(self, body=b"{}"):
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = _W()
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


class _Gmail:
    def __init__(self):
        self.calls = 0

    def save_draft(self, subject, body, to_addr, thread_id=""):
        self.calls += 1
        return {"status": "gmail_draft", "draft_id": "gd-1", "message_id": "gm-1",
                "thread_id": "gt-1", "error": ""}


class TestDashboardGmailDraft(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        contact = self.s.upsert_contact("c1", "Jane", "CTO", "cto", email="jane@acme.com")
        self.draft = self.s.create_draft("c1", "cto", "Subject", "Body", {}, contact_id=contact)
        self.gmail = _Gmail()
        self.dashboard = Dashboard(self.s, gmail=self.gmail)

    def tearDown(self):
        self.s.close()

    def test_save_gmail_draft_persists_id_and_does_not_duplicate_api_call(self):
        first = _Handler()
        self.dashboard._draft_action(first, f"/api/drafts/{self.draft}/save_gmail")
        self.assertEqual(first.status, 200)
        self.assertEqual(self.gmail.calls, 1)
        self.assertEqual(json.loads(first.wfile.data)["draft_id"], "gd-1")

        second = _Handler()
        self.dashboard._draft_action(second, f"/api/drafts/{self.draft}/save_gmail")
        self.assertEqual(second.status, 200)
        self.assertEqual(self.gmail.calls, 1)
        self.assertTrue(json.loads(second.wfile.data)["already_exists"])
    def test_edit_draft_requires_reapproval(self):
        body = json.dumps({"subject": "Edited", "body": "Edited body"}).encode()
        h = _Handler(body)
        self.dashboard._draft_action(h, f"/api/drafts/{self.draft}/edit")
        self.assertEqual(h.status, 200)
        row = self.s.list_drafts()[0]
        self.assertEqual(row["subject"], "Edited")
        self.assertEqual(row["state"], "draft_ready")


if __name__ == "__main__":
    unittest.main()
