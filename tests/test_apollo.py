import unittest

from automatejob.apollo import ApolloConfig, ApolloEnrichment
from automatejob.outreach import OutreachStorage


class FakeUsage:
    def __init__(self, storage):
        self.conn = storage.conn


class TestApollo(unittest.TestCase):
    def test_unavailable_without_key(self):
        ap = ApolloEnrichment(ApolloConfig(api_key=""))
        self.assertFalse(ap.available())
        r = ap.enrich(1, "Jane Doe", "acme.com", "fp")
        self.assertFalse(r.available)
        self.assertEqual(r.email_status, "unavailable")

    def test_budget_enforcement(self):
        s = OutreachStorage(":memory:")
        try:
            ap = ApolloEnrichment(ApolloConfig(api_key="k", daily_budget=1),
                                  usage_store=FakeUsage(s))
            self.assertTrue(ap.available())
            self.assertFalse(ap.budget_exceeded())
            # simulate one credit already used today
            s.conn.execute(
                "INSERT INTO contact_enrichment (contact_id, source, credits_used, enriched_at) "
                "VALUES (1, 'apollo', 1, datetime('now'))")
            s.conn.commit()
            self.assertTrue(ap.budget_exceeded())
            r = ap.enrich(1, "Jane", "acme.com", "fp")
            self.assertIn("budget", r.error)
        finally:
            s.close()

    def test_fingerprint_stable(self):
        a = ApolloEnrichment.fingerprint("Jane Doe", "acme.com")
        b = ApolloEnrichment.fingerprint("jane doe", "ACME.com")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
