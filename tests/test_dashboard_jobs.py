import unittest

from automatejob.dashboard import Dashboard
from automatejob.models import Job
from automatejob.outreach import OutreachStorage
from automatejob.storage import Storage


class TestDashboardJobs(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.jobs = Storage(":memory:")
        self.jobs.upsert_jobs([Job(
            source_type="test", source_token="t", external_id="1",
            title="Software Engineer - AI Platform", company="InferEdge",
            location="Mumbai", url="https://example.com/inferedge", description="2+ years",
            seniority="review", role_family="software_engineering", eligibility="confirmed",
            location_preference="mumbai", actionable=0,
        )])
        self.dashboard = Dashboard(self.outreach, jobs_store=self.jobs)

    def tearDown(self):
        self.outreach.close()
        self.jobs.close()

    def test_relevant_borderline_role_is_visible_for_review(self):
        rows = self.dashboard._jobs()
        self.assertEqual(rows[0]["company"], "InferEdge")
        self.assertEqual(rows[0]["seniority"], "review")
    def test_existing_application_marks_overlapping_yc_source_as_applied(self):
        self.jobs.upsert_jobs([Job(
            source_type="hn", source_token="latest", external_id="2",
            title="Software Engineers", company="MixRank (YC S11)", location="Remote (Global)",
            url="https://example.com/mixrank-hn", description="new grads ok",
            seniority="internship", role_family="software_engineering", eligibility="confirmed",
            location_preference="remote", actionable=1,
        )])
        self.outreach.record_application("mixrank", "Junior Software Engineer", "2026-10-24",
                                         company_name="MixRank")
        rows = self.dashboard._jobs()
        hn = next(r for r in rows if r["company"] == "MixRank (YC S11)")
        self.assertEqual(hn["applied"], "applied")


if __name__ == "__main__":
    unittest.main()
