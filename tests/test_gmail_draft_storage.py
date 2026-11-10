import unittest

from automatejob.outreach import OutreachStorage


class TestGmailDraftStorage(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.contact_id = self.s.upsert_contact(
            "c1", "Jane Doe", "CTO", "cto", email="jane@acme.com", email_status="unverified")
        self.draft_id = self.s.create_draft(
            "c1", "cto", "Subject", "Body", {}, contact_id=self.contact_id)

    def tearDown(self):
        self.s.close()

    def test_gmail_draft_id_is_stored_and_idempotent(self):
        first = self.s.record_gmail_draft(
            self.draft_id, "gmail-draft-1", "gmail-msg-1", "thread-1", "jane@acme.com")
        second = self.s.record_gmail_draft(
            self.draft_id, "gmail-draft-2", "gmail-msg-2", "thread-2", "jane@acme.com")

        self.assertEqual(first["gmail_draft_id"], "gmail-draft-1")
        self.assertEqual(second["gmail_draft_id"], "gmail-draft-1")
        row = self.s.list_drafts()[0]
        self.assertEqual(row["state"], "gmail_draft")
        self.assertEqual(row["gmail_draft_id"], "gmail-draft-1")
        self.assertEqual(row["recipient"], "jane@acme.com")


if __name__ == "__main__":
    unittest.main()
