import unittest

from automatejob.companies import Company, CompanyRegistry, WORLDWIDE_REMOTE
from automatejob.config import (AppConfig, EligibilityConfig, LocationConfig,
                                RoleConfig, ScoringConfig)
from automatejob.eligibility import (
    CONFIRMED, EXCLUDED, NEEDS_VERIFICATION,
    PREF_MUMBAI, PREF_RELOCATION, PREF_REMOTE, classify_eligibility,
)
from automatejob.models import Job


def make_config(relocation=True, cities=None):
    return AppConfig(
        sources=[], roles=[], locations=[],
        scoring=ScoringConfig(),
        eligibility=EligibilityConfig(
            cities=cities or ["mumbai", "thane", "navi mumbai"],
            relocation=relocation,
        ),
    )


def job(location, workplace="", company="Acme", title="DevOps Engineer"):
    return Job("greenhouse", "x", "1", title, company, location,
               "https://example.com/job", workplace_type=workplace)


class TestEligibility(unittest.TestCase):
    def test_mumbai_onsite_confirmed_preferred(self):
        r = classify_eligibility(job("Mumbai, India"), make_config())
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_MUMBAI)

    def test_navi_mumbai_confirmed_preferred(self):
        r = classify_eligibility(job("Navi Mumbai, Maharashtra"), make_config())
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_MUMBAI)

    def test_bengaluru_onsite_is_relocation_queue(self):
        r = classify_eligibility(job("Bengaluru, India"), make_config(relocation=True))
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_RELOCATION)

    def test_bengaluru_onsite_excluded_without_relocation(self):
        r = classify_eligibility(job("Bengaluru, India"), make_config(relocation=False))
        self.assertEqual(r.status, EXCLUDED)

    def test_mumbai_beats_bengaluru(self):
        cfg = make_config()
        self.assertEqual(classify_eligibility(job("Mumbai"), cfg).preference, PREF_MUMBAI)
        self.assertEqual(classify_eligibility(job("Bengaluru"), cfg).preference, PREF_RELOCATION)

    def test_remote_india_confirmed_remote(self):
        r = classify_eligibility(job("Remote - India", "remote"), make_config())
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_REMOTE)

    def test_remote_global_confirmed_remote(self):
        r = classify_eligibility(job("Remote, Global", "remote"), make_config())
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_REMOTE)

    def test_remote_us_excluded(self):
        r = classify_eligibility(job("Remote - US", "remote"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_remote_europe_excluded(self):
        r = classify_eligibility(job("Remote - Europe", "remote"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_bare_remote_needs_verification(self):
        r = classify_eligibility(job("Remote", "remote"), make_config())
        self.assertEqual(r.status, NEEDS_VERIFICATION)

    def test_apac_remote_needs_verification(self):
        r = classify_eligibility(job("Remote - APAC", "remote"), make_config())
        self.assertEqual(r.status, NEEDS_VERIFICATION)

    def test_onsite_foreign_excluded(self):
        r = classify_eligibility(job("San Francisco, California"), make_config())
        self.assertEqual(r.status, EXCLUDED)

    def test_foreign_city_only_is_excluded(self):
        # A foreign CITY name without a country suffix must still be excluded.
        for loc in ["Dublin", "Barcelona", "Toronto", "Seoul", "Berlin",
                    "Sydney", "Austin, TX", "Palo Alto, CA"]:
            self.assertEqual(classify_eligibility(job(loc), make_config()).status,
                             EXCLUDED, loc)

    def test_hybrid_mumbai_confirmed_preferred(self):
        r = classify_eligibility(job("Mumbai", "hybrid"), make_config())
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_MUMBAI)

    def test_registry_upgrades_worldwide_remote(self):
        reg = CompanyRegistry([Company(name="Supabase", remote_eligibility=WORLDWIDE_REMOTE)])
        r = classify_eligibility(job("Remote", "remote", company="Supabase"),
                                 make_config(), registry=reg)
        self.assertEqual(r.status, CONFIRMED)
        self.assertEqual(r.preference, PREF_REMOTE)


if __name__ == "__main__":
    unittest.main()
