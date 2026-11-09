import unittest

from automatejob.companies import Company, CompanyRegistry, WORLDWIDE_REMOTE
from automatejob.config import AppConfig, EligibilityConfig, LocationConfig, RoleConfig, ScoringConfig
from automatejob.eligibility import (CONFIRMED, EXCLUDED, NEEDS_VERIFICATION,
                                     classify_eligibility)
from automatejob.models import Job


def make_config(india_wide=True, relocation=True, cities=None):
    return AppConfig(
        sources=[], roles=[], locations=[],
        scoring=ScoringConfig(),
        eligibility=EligibilityConfig(
            cities=cities or ["mumbai", "thane", "navi mumbai"],
            india_wide=india_wide, relocation=relocation,
        ),
    )


def job(location, workplace="", company="Acme", title="DevOps Engineer"):
    return Job("greenhouse", "x", "1", title, company, location,
               "https://example.com/job", workplace_type=workplace)


class TestEligibility(unittest.TestCase):
    def test_mumbai_onsite_is_confirmed(self):
        r = classify_eligibility(job("Mumbai, India"), make_config())
        self.assertEqual(r.status, CONFIRMED)

    def test_navi_mumbai_is_confirmed(self):
        r = classify_eligibility(job("Navi Mumbai, Maharashtra"), make_config())
        self.assertEqual(r.status, CONFIRMED)

    def test_bengaluru_onsite_is_confirmed_when_india_wide(self):
        r = classify_eligibility(job("Bengaluru, India"), make_config(india_wide=True))
        self.assertEqual(r.status, CONFIRMED)

    def test_bengaluru_onsite_needs_verification_when_mumbai_only(self):
        r = classify_eligibility(job("Bengaluru, India"), make_config(india_wide=False))
        self.assertEqual(r.status, NEEDS_VERIFICATION)

    def test_mumbai_beats_bengaluru_when_mumbai_only(self):
        # With Mumbai-only preference, a Mumbai role is confirmed while a
        # Bengaluru role is not.
        cfg = make_config(india_wide=False)
        self.assertEqual(classify_eligibility(job("Mumbai"), cfg).status, CONFIRMED)
        self.assertEqual(classify_eligibility(job("Bengaluru"), cfg).status, NEEDS_VERIFICATION)

    def test_remote_india_is_confirmed(self):
        r = classify_eligibility(job("Remote - India", "remote"), make_config())
        self.assertEqual(r.status, CONFIRMED)

    def test_remote_global_is_confirmed(self):
        r = classify_eligibility(job("Remote, Global", "remote"), make_config())
        self.assertEqual(r.status, CONFIRMED)

    def test_remote_us_is_excluded(self):
        r = classify_eligibility(job("Remote - US", "remote"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_remote_europe_is_excluded(self):
        r = classify_eligibility(job("Remote - Europe", "remote"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_ambiguous_bare_remote_is_needs_verification(self):
        r = classify_eligibility(job("Remote", "remote"), make_config())
        self.assertEqual(r.status, NEEDS_VERIFICATION)

    def test_apac_remote_is_needs_verification(self):
        # APAC could include India but is not specific enough to confirm.
        r = classify_eligibility(job("Remote - APAC", "remote"), make_config())
        self.assertEqual(r.status, NEEDS_VERIFICATION)

    def test_onsite_foreign_is_excluded(self):
        r = classify_eligibility(job("San Francisco, California"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_hybrid_mumbai_is_confirmed(self):
        r = classify_eligibility(job("Mumbai", "hybrid"), make_config())
        self.assertEqual(r.status, CONFIRMED)

    def test_registry_upgrades_worldwide_remote(self):
        reg = CompanyRegistry([Company(name="Supabase", remote_eligibility=WORLDWIDE_REMOTE)])
        r = classify_eligibility(job("Remote", "remote", company="Supabase"), make_config(), registry=reg)
        self.assertEqual(r.status, CONFIRMED)


if __name__ == "__main__":
    unittest.main()
