import unittest
from datetime import date

from automatejob.outreach import (
    CONTACTED, DRAFT_READY, OutreachStorage, add_business_days,
    followup_due_date, is_business_day,
)


class TestBusinessDays(unittest.TestCase):
    def test_is_business_day(self):
        self.assertTrue(is_business_day(date(2026, 11, 10)))   # Tue
        self.assertFalse(is_business_day(date(2026, 11, 14)))  # Sat
        self.assertFalse(is_business_day(date(2026, 11, 15)))  # Sun

    def test_add_business_days_skips_weekend(self):
        # Fri Nov 13 2026 + 1 business day = Mon Nov 16
        self.assertEqual(add_business_days(date(2026, 11, 13), 1), date(2026, 11, 16))

    def test_followup_due_dates(self):
        # sent Monday -> first followup ~5 business days later
        d = followup_due_date(date(2026, 11, 9), 1)  # Mon
        self.assertEqual(d.weekday(), 4)  # lands on a Friday


class TestOutreachStorage(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")

    def tearDown(self):
        self.s.close()

    def test_company_transition_audited(self):
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal_speculative", 50, [])
        self.assertTrue(self.s.transition_company("c1", "researched"))
        row = self.s.conn.execute(
            "SELECT * FROM audit_events WHERE entity='company' AND entity_id='c1'").fetchone()
        self.assertEqual(row["from_state"], "discovered")
        self.assertEqual(row["to_state"], "researched")

    def test_contact_dedup(self):
        a = self.s.upsert_contact("c1", "Jane Doe", "CTO", "cto",
                                  email="jane@acme.com", email_status="verified")
        b = self.s.upsert_contact("c1", "Jane Doe", "CEO", "founder")
        self.assertEqual(a, b)
        self.assertEqual(len(self.s.list_contacts("c1")), 1)

    def test_followup_suppressed_after_reply(self):
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.s.upsert_thread("t1", "c1", None)
        self.s.create_followup("t1", "c1", None, 1, "2026-11-01", "no reply")
        # no reply yet -> due
        self.assertEqual(len(self.s.list_due_followups()), 1)
        # a substantive reply arrives -> suppressed
        self.s.record_reply("t1", "reply")
        self.assertEqual(len(self.s.list_due_followups()), 0)

    def test_followup_suppressed_after_optout(self):
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.s.upsert_thread("t1", "c1", None)
        self.s.create_followup("t1", "c1", None, 1, "2026-11-01", "no reply")
        self.s.record_reply("t1", "optout")
        self.assertEqual(len(self.s.list_due_followups()), 0)

    def test_draft_approval(self):
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        did = self.s.create_draft("c1", "vacancy", "Subject", "Body", {})
        self.assertTrue(self.s.approve_draft(did))
        drafts = self.s.list_drafts(state="approved")
        self.assertEqual(len(drafts), 1)

    def test_draft_can_be_edited_before_sending(self):
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        did = self.s.create_draft("c1", "vacancy", "Old subject", "Old body", {})
        self.assertTrue(self.s.update_draft(did, "New subject", "New body"))
        row = self.s.list_drafts()[0]
        self.assertEqual(row["subject"], "New subject")
        self.assertEqual(row["body"], "New body")


if __name__ == "__main__":
    unittest.main()
