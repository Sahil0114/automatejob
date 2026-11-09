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


def job(title, location, workplace="", employment=""):
    return Job("greenhouse", "x", "1", title, "Acme", location,
               "https://example.com/job", workplace_type=workplace,
               employment_type=employment)


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.scorer = Scorer(make_config())

    def test_junior_devops_mumbai_scores_high_and_explainable(self):
        score, reasons = self.scorer.score(job("Junior DevOps Engineer", "Mumbai, India"))
        factors = {r["factor"]: r for r in reasons}
        self.assertIn("role", factors)
        self.assertIn("location", factors)
        self.assertIn("seniority", factors)
        # role base 18 (devops) + location 35 (mumbai) + seniority 15 (junior) = 68
        self.assertEqual(score, 68.0)
        self.assertEqual(factors["location"]["points"], 35.0)
        self.assertEqual(factors["seniority"]["points"], 15.0)
        self.assertEqual(job("Junior DevOps Engineer", "Mumbai, India").seniority, "")

    def test_senior_remote_us_is_excluded(self):
        j = job("Senior Staff Engineer", "Remote - US")
        score, reasons = self.scorer.score(j)
        # role 0, location excluded -> not scored, eligibility excluded
        self.assertEqual(score, 0.0)
        self.assertEqual(j.eligibility, "excluded")
        self.assertTrue(any(r["factor"] == "eligibility" for r in reasons))

    def test_bare_remote_is_needs_verification(self):
        j = job("DevOps Engineer", "Remote")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "needs_verification")
        # role 18 + remote-ambiguous 5 + neutral seniority 8 = 31
        self.assertEqual(score, 31.0)
        loc = [r for r in reasons if r["factor"] == "location"][0]
        self.assertEqual(loc["points"], 5.0)

    def test_worldwide_remote_is_confirmed(self):
        j = job("DevOps Engineer", "Remote - Global")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "confirmed")
        loc = [r for r in reasons if r["factor"] == "location"][0]
        self.assertEqual(loc["points"], 15.0)

    def test_internship_bonus(self):
        score, reasons = self.scorer.score(job("Software Engineering Intern", "India"))
        intern = [r for r in reasons if r["factor"] == "internship"][0]
        self.assertEqual(intern["points"], 10.0)
        # role 22 + location india 30 + seniority junior 15 + internship 10 = 77
        self.assertEqual(score, 77.0)

    def test_short_keyword_word_boundary(self):
        # "go"/"ai" must not match inside unrelated words
        score, reasons = self.scorer.score(job("Algorithms Analyst", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertEqual(role["points"], 0.0)

    def test_go_backend_match(self):
        score, reasons = self.scorer.score(job("Backend Engineer (Go)", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        # "backend" (base) + "go" (extra keyword) -> 18 + 4 = 22
        self.assertEqual(role["points"], 22.0)

    def test_remote_us_state_is_excluded(self):
        j = job("DevOps Engineer", "Remote - Boston, MA")
        score, reasons = self.scorer.score(j)
        self.assertEqual(j.eligibility, "excluded")
        self.assertEqual(score, 0.0)

    def test_irrelevant_role_is_capped(self):
        # no target role + no internship -> capped at irrelevant_cap (20)
        score, reasons = self.scorer.score(job("Associate Recruiter", "India"))
        self.assertEqual(score, 20.0)
        self.assertTrue(any(r["factor"] == "gate" for r in reasons))

    def test_plain_software_engineer_matches_role(self):
        score, reasons = self.scorer.score(job("Software Engineer", "Remote"))
        role = [r for r in reasons if r["factor"] == "role"][0]
        self.assertGreater(role["points"], 0.0)


if __name__ == "__main__":
    unittest.main()
