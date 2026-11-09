import unittest

from automatejob.config import (AppConfig, EligibilityConfig, LocationConfig,
                                RoleConfig, ScoringConfig)
from automatejob.models import Job
from automatejob.scoring import Scorer


def make_config():
    roles = [
        RoleConfig("devops", "Junior DevOps / SRE / Infra",
                   ["devops", "site reliability", "sre", "kubernetes"]),
        RoleConfig("go_backend", "Go Backend Engineering",
                   ["go", "golang", "backend", "api", "microservices",
                    "software engineer", "software engineering"]),
    ]
    locations = [
        LocationConfig("mumbai", "Mumbai", ["mumbai", "maharashtra"], 35),
        LocationConfig("india", "India", ["india", "bengaluru"], 30),
    ]
    return AppConfig(sources=[], roles=roles, locations=locations,
                     scoring=ScoringConfig(), eligibility=EligibilityConfig())


def job(title, location, workplace="", employment="", description=""):
    return Job("greenhouse", "x", "1", title, "Acme", location,
               "https://example.com/job", workplace_type=workplace,
               employment_type=employment, description=description)


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.scorer = Scorer(make_config())

    def test_junior_devops_mumbai_scores_high_and_explainable(self):
        score, reasons = self.scorer.score(job("Junior DevOps Engineer", "Mumbai, India"))
        factors = {r["factor"]: r for r in reasons}
        self.assertIn("role", factors)
        self.assertIn("location", factors)
        self.assertIn("seniority", factors)
        # role 18 + location 35 (mumbai) + seniority 15 (junior) = 68
        self.assertEqual(score, 68.0)
        self.assertEqual(factors["location"]["points"], 35.0)
        self.assertEqual(factors["seniority"]["points"], 15.0)

    def test_senior_remote_us_is_excluded(self):
        j = job("Senior Staff Engineer", "Remote - US")
        score, reasons = self.scorer.score(j)
        self.assertEqual(score, 0.0)
        self.assertEqual(j.eligibility, "excluded")

    def test_bare_remote_is_needs_verification(self):
        j = job("DevOps Engineer", "Remote")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "needs_verification")
        loc = [r for r in reasons if r["factor"] == "location"][0]
        self.assertEqual(loc["points"], 5.0)

    def test_worldwide_remote_is_confirmed(self):
        j = job("DevOps Engineer", "Remote - Global", "remote")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "confirmed")
        self.assertEqual(j.location_preference, "remote")
        loc = [r for r in reasons if r["factor"] == "location"][0]
        self.assertEqual(loc["points"], 15.0)

    def test_non_engineering_title_gets_zero_role_points(self):
        # A recruiter whose description mentions engineering keywords must not
        # earn role points.
        j = job("Associate Recruiter", "Mumbai", description="recruit for DevOps and cloud teams")
        score, reasons = self.scorer.score(j)
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertEqual(role["points"], 0.0)
        self.assertEqual(j.role_family, "non_engineering")

    def test_short_keyword_word_boundary(self):
        score, reasons = self.scorer.score(job("Algorithms Analyst", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertEqual(role["points"], 0.0)

    def test_go_backend_match(self):
        score, reasons = self.scorer.score(job("Backend Engineer (Go)", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertEqual(role["points"], 22.0)

    def test_remote_us_state_is_excluded(self):
        j = job("DevOps Engineer", "Remote - Boston, MA")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "excluded")
        self.assertEqual(score, 0.0)

    def test_irrelevant_role_is_capped(self):
        score, reasons = self.scorer.score(job("Associate Recruiter", "India"))
        self.assertEqual(score, 20.0)
        self.assertTrue(any(r["factor"] == "gate" for r in reasons))

    def test_plain_software_engineer_matches_role(self):
        score, reasons = self.scorer.score(job("Software Engineer", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertGreater(role["points"], 0.0)


if __name__ == "__main__":
    unittest.main()
