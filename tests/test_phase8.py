import unittest

from automatejob.outreach import MSG_SENT, OutreachStorage
from automatejob.profile import CandidateProfile
from automatejob.workflow import add_contact_and_draft


def profile():
    return CandidateProfile("data/candidate_profile.toml")


class TestApplications(unittest.TestCase):
    def setUp(self):
        self.o = OutreachStorage(":memory:")
        self.o.upsert_company("mixrank", "MixRank", "mixrank.com", "active_vacancy", 70, [])

    def tearDown(self):
        self.o.close()

    def test_record_application_dedup(self):
        a1 = self.o.record_application("mixrank", "Junior SWE", "2026-10-24")
        a2 = self.o.record_application("mixrank", "Junior SWE", "2026-10-24")
        self.assertEqual(a1, a2)
        self.assertEqual(len(self.o.list_applications()), 1)

    def test_has_application(self):
        self.assertFalse(self.o.has_application("mixrank"))
        self.o.record_application("mixrank", "Junior SWE", "2026-10-24")
        self.assertTrue(self.o.has_application("mixrank"))

    def test_existing_application_generates_followup(self):
        self.o.record_application("mixrank", "Junior SWE", "2026-10-24")
        r = add_contact_and_draft(self.o, profile(), "mixrank", "Scott Milliken",
                                  "Founder", "founder", "scott@mixrank.com")
        self.assertEqual(r.draft["draft_type"] if "draft_type" in r.draft
                         else r.draft.get("subject", "").lower(), r.draft.get("subject", "").lower())
        # a follow-up (not a cold "Idea for ..." application) is generated
        self.assertIn("following up", r.draft["subject"].lower())
        self.assertTrue(any("follow-up" in w for w in r.warnings))


class TestManualSend(unittest.TestCase):
    def setUp(self):
        self.o = OutreachStorage(":memory:")
        self.o.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.did = self.o.create_draft("c1", "founder", "Subj", "Body", {})

    def tearDown(self):
        self.o.close()

    def test_mark_sent_requires_approval(self):
        with self.assertRaises(ValueError):
            self.o.mark_sent_manually(self.did, "2026-11-09T10:00:00Z")

    def test_mark_sent_manually_records_timestamp(self):
        self.o.approve_draft(self.did)
        msg_id = self.o.mark_sent_manually(self.did, "2026-11-09T10:00:00Z")
        row = self.o.conn.execute(
            "SELECT * FROM outbound_messages WHERE id=?", (msg_id,)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_at"], "2026-11-09T10:00:00Z")
        draft = self.o.conn.execute(
            "SELECT state FROM outreach_drafts WHERE id=?", (self.did,)).fetchone()
        self.assertEqual(draft["state"], "sent")

    def test_mark_sent_idempotent_via_record_send(self):
        self.o.approve_draft(self.did)
        m1 = self.o.mark_sent_manually(self.did, "2026-11-09T10:00:00Z")
        # second call on the same draft is not a new outbound message
        m2 = self.o.mark_sent_manually(self.did, "2026-11-09T11:00:00Z")
        self.assertEqual(m1, m2)


class TestContactCompanyAssociation(unittest.TestCase):
    def setUp(self):
        self.o = OutreachStorage(":memory:")
        self.o.upsert_company("acme-hash", "Acme", "acme.com", "high_signal", 50, [])
        self.o.upsert_company("globex-hash", "Globex", "globex.com", "high_signal", 50, [])

    def tearDown(self):
        self.o.close()

    def test_contact_resolves_canonical_company_by_domain(self):
        r = add_contact_and_draft(self.o, profile(), "acme.com", "Jane", "CTO",
                                  "cto", "jane@acme.com")
        contact = self.o.list_contacts("acme-hash")
        self.assertEqual(len(contact), 1)
        self.assertEqual(contact[0]["company_id"], "acme-hash")

    def test_contact_email_domain_mismatch_warns_for_manual_review(self):
        r = add_contact_and_draft(self.o, profile(), "acme.com", "Jane", "CTO",
                                  "cto", "jane@globex.com")
        self.assertTrue(any("domain does not match" in w for w in r.warnings))

    def test_contact_not_misassociated(self):
        # email for Acme must not attach to Globex
        r = add_contact_and_draft(self.o, profile(), "globex.com", "John", "EM",
                                  "engineering_manager", "john@globex.com")
        acme_contacts = self.o.list_contacts("acme-hash")
        self.assertEqual(len(acme_contacts), 0)
class TestAuditedJobRegressions(unittest.TestCase):
    def setUp(self):
        from automatejob.config import AppConfig
        self.cfg = AppConfig.load("config.toml")

    def test_inferedge_two_plus_years_stays_review_not_actionable(self):
        from automatejob.models import Job
        from automatejob.qualification import classify_job
        job = Job(
            source_type="yc_waas", source_token="inferedge", external_id="120102",
            title="Software Engineer - AI Platform", company="InferEdge", location="Mumbai, MH, IN",
            url="https://www.workatastartup.com/jobs/120102",
            description="Software Engineer - AI Platform | 2+ years | AI platform", freshness="fresh")
        classify_job(job, self.cfg, None)
        self.assertEqual(job.seniority, "review")
        self.assertEqual(job.actionable, 0)

    def test_zepto_legacy_listing_needs_verification_not_actionable(self):
        from automatejob.models import Job
        from automatejob.qualification import classify_job
        job = Job(
            source_type="yc_waas", source_token="zepto", external_id="43469",
            title="Founding Backend Engineer", company="Zepto", location="Mumbai, India / Remote",
            url="https://www.workatastartup.com/jobs/43469",
            description="Founding Backend Engineer", freshness="needs_verification")
        classify_job(job, self.cfg, None)
        self.assertEqual(job.actionable, 0)


if __name__ == "__main__":
    unittest.main()
