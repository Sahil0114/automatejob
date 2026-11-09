import unittest

from automatejob.company import (
    CAT_ACTIVE_HIRING, CAT_SPECULATIVE, Company, CompanyStorage,
    REMOTE_INDIA, REMOTE_WORLDWIDE, make_company_key, normalize_domain,
)
from automatejob.company_scoring import score_company


def make_company(name="Acme", website="https://acme.com", **kw):
    defaults = dict(name=name, website=website)
    defaults.update(kw)
    return Company(**defaults)


class TestNormalizeDomain(unittest.TestCase):
    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("https://www.Acme.com/"), "acme.com")
        self.assertEqual(normalize_domain("acme.com"), "acme.com")
        self.assertEqual(normalize_domain("HTTPS://Acme.com/careers"), "acme.com")
        self.assertEqual(normalize_domain(""), "")

    def test_company_key_normalizes(self):
        a = make_company(name="Acme, Inc.", website="https://ACME.com")
        b = make_company(name="Acme", website="acme.com")
        self.assertEqual(a.dedup_key, b.dedup_key)


class TestCompanyStorage(unittest.TestCase):
    def setUp(self):
        self.s = CompanyStorage(":memory:")

    def tearDown(self):
        self.s.close()

    def test_upsert_and_dedup(self):
        a = make_company(name="Acme", website="https://acme.com",
                         office_locations=["Mumbai"], mumbai_office=True)
        b = make_company(name="Beta", website="https://beta.io",
                         office_locations=["Bengaluru"])
        self.assertEqual(self.s.upsert([a, b]), 2)
        self.assertEqual(self.s.count(), 2)
        # re-upsert same key -> merge, no new row
        self.assertEqual(self.s.upsert([a]), 0)
        self.assertEqual(self.s.count(), 2)

    def test_unknown_fields_stored_as_none(self):
        c = make_company()  # no mumbai / remote info
        self.s.upsert([c])
        r = self.s.list()[0]
        self.assertIsNone(r["mumbai_office"])
        self.assertEqual(r["remote_policy"], "unknown")

    def test_deduplicate_merges_same_domain(self):
        # Force two rows with the same domain but distinct dedup keys.
        self.s.conn.execute("INSERT INTO companies (dedup_key,name,domain,website,"
                            "office_locations,technologies,sources,first_seen_at,last_seen_at) "
                            "VALUES ('k1','Acme','acme.com','https://acme.com','[\"Mumbai\"]','[]','[]','','')")
        self.s.conn.execute("INSERT INTO companies (dedup_key,name,domain,website,"
                            "office_locations,technologies,sources,first_seen_at,last_seen_at) "
                            "VALUES ('k2','Acme Corp','acme.com','https://acme.com','[]','[\"Go\"]','[]','','')")
        self.s.conn.commit()
        removed = self.s.deduplicate()
        self.assertEqual(removed, 1)
        self.assertEqual(self.s.count(), 1)
        r = self.s.list()[0]
        self.assertIn("Mumbai", __import__("json").loads(r["office_locations"]))


class TestCompanyScoring(unittest.TestCase):
    def test_mumbai_remote_tech_is_speculative(self):
        c = make_company(mumbai_office=True, remote_policy=REMOTE_WORLDWIDE,
                         technologies=["Go", "Kubernetes"], funding_stage="YC W24")
        score_company(c, has_relevant_opening=False)
        self.assertEqual(c.category, CAT_SPECULATIVE)
        self.assertGreater(c.score, 30)

    def test_active_hiring_beats_speculative(self):
        c = make_company(mumbai_office=True)
        score_company(c, has_relevant_opening=True)
        self.assertEqual(c.category, CAT_ACTIVE_HIRING)

    def test_no_signal_is_research_lead(self):
        c = make_company()
        score_company(c, has_relevant_opening=False)
        self.assertEqual(c.category, "research_lead")


if __name__ == "__main__":
    unittest.main()
