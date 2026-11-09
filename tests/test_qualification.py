"""Tests for the actionable gate: a job is actionable only if it passes ALL of
open + target role family + junior seniority + acceptable location/remote."""

import unittest

from automatejob.config import (AppConfig, EligibilityConfig, LocationConfig,
                                RoleConfig, ScoringConfig)
from automatejob.models import Job
from automatejob.qualification import (
    CAT_ACTIONABLE, CAT_CLOSED, CAT_EXCLUDED, CAT_NEEDS_VERIFICATION,
    CAT_RELOCATION, CAT_REVIEW, categorize, classify_job,
)


def make_config(relocation=True):
    roles = [RoleConfig("devops", "DevOps/SRE", ["devops", "sre", "kubernetes"]),
             RoleConfig("go_backend", "Go Backend", ["go", "backend", "software engineer"])]
    locations = [LocationConfig("mumbai", "Mumbai", ["mumbai"], 35),
                 LocationConfig("india", "India", ["india", "bengaluru"], 30)]
    return AppConfig(sources=[], roles=roles, locations=locations,
                     scoring=ScoringConfig(),
                     eligibility=EligibilityConfig(cities=["mumbai", "thane", "navi mumbai"],
                                                   relocation=relocation))


def job(title, location, workplace="", description="", employment=""):
    return Job("greenhouse", "x", "1", title, "Acme", location,
               "https://example.com/job", workplace_type=workplace,
               employment_type=employment, description=description)


class TestActionableGate(unittest.TestCase):
    def test_junior_engineering_mumbai_is_actionable(self):
        c = classify_job(job("Junior DevOps Engineer", "Mumbai, India"), make_config())
        self.assertTrue(c.actionable)
        self.assertEqual(c.location_preference, "mumbai")

    def test_junior_engineering_remote_india_is_actionable(self):
        c = classify_job(job("Junior DevOps Engineer", "Remote - India", "remote"), make_config())
        self.assertTrue(c.actionable)
        self.assertEqual(c.location_preference, "remote")

    def test_junior_engineering_worldwide_remote_is_actionable(self):
        c = classify_job(job("Software Engineer", "Remote, Global", "remote",
                             description="0-2 years of experience"), make_config())
        self.assertTrue(c.actionable)

    def test_junior_engineering_bengaluru_is_relocation_not_actionable(self):
        c = classify_job(job("Junior DevOps Engineer", "Bengaluru, India"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.location_preference, "relocation")

    def test_senior_engineering_mumbai_is_not_actionable(self):
        c = classify_job(job("Senior DevOps Engineer", "Mumbai"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.seniority, "senior")

    def test_intermediate_engineering_is_not_actionable(self):
        c = classify_job(job("Software Engineer 3", "Mumbai"), make_config())
        self.assertFalse(c.actionable)

    def test_lead_engineering_is_not_actionable(self):
        c = classify_job(job("Engineering Manager", "Mumbai"), make_config())
        self.assertFalse(c.actionable)

    def test_non_engineering_junior_mumbai_is_not_actionable(self):
        c = classify_job(job("Associate Recruiter", "Mumbai"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.role_family, "non_engineering")

    def test_engineering_junior_foreign_is_not_actionable(self):
        c = classify_job(job("Junior DevOps Engineer", "San Francisco, CA"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.eligibility, "excluded")

    def test_engineering_unknown_seniority_remote_is_not_actionable(self):
        # no experience signal -> uncertain -> review, not actionable
        c = classify_job(job("Software Engineer", "Remote, Global", "remote"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.seniority, "unknown")

    def test_engineering_bare_remote_is_needs_verification(self):
        c = classify_job(job("Junior DevOps Engineer", "Remote", "remote"), make_config())
        self.assertFalse(c.actionable)
        self.assertEqual(c.eligibility, "needs_verification")


class TestCategorize(unittest.TestCase):
    def test_partition_is_non_overlapping_and_ordered(self):
        # closed wins over everything.
        self.assertEqual(categorize("closed", "confirmed", "remote", 1), CAT_CLOSED)
        # excluded / needs_verification beat actionable.
        self.assertEqual(categorize("open", "excluded", "", 0), CAT_EXCLUDED)
        self.assertEqual(categorize("open", "needs_verification", "", 0), CAT_NEEDS_VERIFICATION)
        # actionable beats relocation.
        self.assertEqual(categorize("open", "confirmed", "remote", 1), CAT_ACTIONABLE)
        # relocation before review.
        self.assertEqual(categorize("open", "confirmed", "relocation", 0), CAT_RELOCATION)
        # confirmed + mumbai/remote + not actionable -> review.
        self.assertEqual(categorize("open", "confirmed", "mumbai", 0), CAT_REVIEW)
        self.assertEqual(categorize("open", "confirmed", "remote", 0), CAT_REVIEW)


if __name__ == "__main__":
    unittest.main()
