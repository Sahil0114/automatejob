import unittest

from automatejob.dashboard import Dashboard
from automatejob.models import Job
from automatejob.outreach import OutreachStorage
from automatejob.storage import Storage


class TestCompanyResearchDashboard(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.jobs = Storage(":memory:")
        self.outreach.upsert_company("mixrank", "MixRank", "mixrank.com", "active_vacancy", 70, [])
        self.outreach.add_evidence("mixrank", "hiring", "New grads ok", "https://example.com/job")
        self.outreach.upsert_contact("mixrank", "Scott Milliken", "Founder", "founder",
                                     email="", email_status="unavailable",
                                     linkedin_url="https://linkedin.example/scott",
                                     source_url="https://example.com/team")
        self.jobs.upsert_jobs([Job(
            source_type="test", source_token="t", external_id="j1",
            title="Junior Software Engineer", company="MixRank", location="Remote (Global)",
            url="https://example.com/job", workplace_type="remote",
            description="new grads ok", seniority="junior", role_family="software_engineering",
            eligibility="confirmed", location_preference="remote", actionable=1,
        )])
        self.dashboard = Dashboard(self.outreach, jobs_store=self.jobs)

    def tearDown(self):
        self.outreach.close()
        self.jobs.close()

    def test_company_research_includes_evidence_contacts_vacancies_and_next_action(self):
        rows = self.dashboard._company_research()
        mixrank = next(r for r in rows if r["company_id"] == "mixrank")
        self.assertEqual(mixrank["evidence"][0]["url"], "https://example.com/job")
        self.assertEqual(mixrank["contacts"][0]["full_name"], "Scott Milliken")
        self.assertEqual(mixrank["jobs"][0]["title"], "Junior Software Engineer")
        self.assertIn("email", mixrank["suggested_next_action"].lower())


if __name__ == "__main__":
    unittest.main()
