import os
import tempfile
import unittest

from automatejob.gmail import GmailAdapter, GmailConfig
from automatejob.outreach import OutreachStorage
from automatejob.profile import CandidateProfile
from automatejob.workflow import (
    add_contact_and_draft, import_contacts_csv, send_draft, validate_email,
)


def profile():
    return CandidateProfile("data/candidate_profile.toml")


class TestValidateEmail(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_email("jane@acme.com"))

    def test_invalid(self):
        self.assertFalse(validate_email("not-an-email"))
        self.assertFalse(validate_email(""))
        self.assertFalse(validate_email("jane@"))


class TestManualContactWorkflow(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.outreach.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.prof = profile()

    def tearDown(self):
        self.outreach.close()

    def test_add_contact_generates_role_draft(self):
        r = add_contact_and_draft(self.outreach, self.prof, "c1", "Jane CTO",
                                  "CTO", "cto", "jane@acme.com")
        self.assertGreater(r.contact_id, 0)
        self.assertIn("Sahil Gada", r.draft["body"])
        self.assertIn("Acme", r.draft["body"])  # company referenced, email NOT leaked into body
        self.assertNotIn("jane@acme.com", r.draft["body"])

    def test_manual_email_marked_unverified(self):
        r = add_contact_and_draft(self.outreach, self.prof, "c1", "Jane", "CTO",
                                  "cto", "jane@acme.com")
        self.assertTrue(any("unverified" in w for w in r.warnings))
        contact = self.outreach.list_contacts("c1")[0]
        self.assertEqual(contact["email_status"], "unverified")

    def test_duplicate_email_warns(self):
        add_contact_and_draft(self.outreach, self.prof, "c1", "Jane", "CTO", "cto",
                              "jane@acme.com")
        r2 = add_contact_and_draft(self.outreach, self.prof, "c1", "Other Person",
                                   "EM", "engineering_manager", "jane@acme.com")
        self.assertTrue(any("already belongs" in w for w in r2.warnings))

    def test_suppressed_company_warns(self):
        self.outreach.suppress("company", "c1", "do not contact")
        r = add_contact_and_draft(self.outreach, self.prof, "c1", "Jane", "CTO",
                                  "cto", "jane@acme.com")
        self.assertTrue(any("suppressed" in w for w in r.warnings))

    def test_invalid_email_format_warns(self):
        r = add_contact_and_draft(self.outreach, self.prof, "c1", "Jane", "CTO",
                                  "cto", "not-an-email")
        self.assertTrue(any("format" in w for w in r.warnings))


class TestCSVImport(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.outreach.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.prof = profile()
        self.path = os.path.join(tempfile.gettempdir(), "contacts_test.csv")

    def tearDown(self):
        self.outreach.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_import_valid_and_invalid(self):
        with open(self.path, "w") as f:
            f.write("company_id,name,title,role_kind,email\n")
            f.write("c1,Jane CTO,CTO,cto,jane@acme.com\n")
            f.write("c1,Bad Email,EM,engineering_manager,not-an-email\n")
            f.write(",No Company,EM,engineering_manager,ok@acme.com\n")
        result = import_contacts_csv(self.outreach, self.prof, self.path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], 2)


class TestSendGating(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.outreach.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.draft_id = self.outreach.create_draft("c1", "founder", "Subj", "Body", {})

    def tearDown(self):
        self.outreach.close()

    def test_send_blocked_when_disabled(self):
        gmail = GmailAdapter(GmailConfig(), sending_enabled=False)
        r = send_draft(self.outreach, gmail, self.draft_id, "j@acme.com",
                       sending_enabled=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "sending_disabled")

    def test_send_requires_approval(self):
        gmail = GmailAdapter(GmailConfig(), sending_enabled=True)
        r = send_draft(self.outreach, gmail, self.draft_id, "j@acme.com",
                       sending_enabled=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "not_approved")

    def test_send_blocked_without_gmail(self):
        gmail = GmailAdapter(GmailConfig())  # no credentials
        self.outreach.approve_draft(self.draft_id)
        r = send_draft(self.outreach, gmail, self.draft_id, "j@acme.com",
                       sending_enabled=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "gmail_unavailable")

    def test_record_send_is_idempotent(self):
        m1 = self.outreach.record_send(self.draft_id, None, "c1", "Subj", "Body")
        m2 = self.outreach.record_send(self.draft_id, None, "c1", "Subj", "Body")
        self.assertEqual(m1, m2)


if __name__ == "__main__":
    unittest.main()
