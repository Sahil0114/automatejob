import unittest

from automatejob.source_registry import (
    IntegrationStatus, SourceRegistry, SourceStatus, SOURCE_CATALOG, seed_defaults,
)

# The 19 discovery sources explicitly required by the phase spec.
REQUIRED_19 = {
    "linkedin", "internshala", "indeed", "naukri", "wellfound", "foundit",
    "cutshort", "hirist", "uplers", "instahyre", "yc_waas", "weworkremotely",
    "jobfound", "hn", "flexjobs", "gethnjobs", "devopshunt", "theantijobboard", "x",
}


class TestSourceRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = SourceRegistry(":memory:")

    def tearDown(self):
        self.reg.close()

    def test_catalog_covers_all_19_required_sources(self):
        names = {c["name"] for c in SOURCE_CATALOG}
        self.assertTrue(REQUIRED_19.issubset(names),
                        f"missing sources: {REQUIRED_19 - names}")

    def test_catalog_has_unique_names(self):
        names = [c["name"] for c in SOURCE_CATALOG]
        self.assertEqual(len(names), len(set(names)))

    def test_catalog_statuses_and_integration_statuses_are_valid(self):
        for c in SOURCE_CATALOG:
            self.assertIn(c["status"], (SourceStatus.ACTIVE, SourceStatus.PAUSED, SourceStatus.UNSUPPORTED),
                          c["name"])
            self.assertIn(c["integration_status"], IntegrationStatus.ALL, c["name"])

    def test_seed_defaults_is_idempotent_and_populates_all(self):
        n1 = seed_defaults(self.reg)
        n2 = seed_defaults(self.reg)
        self.assertEqual(n1, n2)
        self.assertGreaterEqual(n1, len(REQUIRED_19))
        names = {r["name"] for r in self.reg.list_all()}
        self.assertTrue(REQUIRED_19.issubset(names))

    def test_no_required_source_is_silently_unsupported_without_reason(self):
        seed_defaults(self.reg)
        for name in REQUIRED_19:
            row = self.reg.get(name)
            self.assertIsNotNone(row, name)
            if row["status"] in (SourceStatus.PAUSED, SourceStatus.UNSUPPORTED):
                self.assertTrue(row["reason"], f"{name} has no reason")

    def test_upsert_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            self.reg.upsert("bad", "x", status="bogus")

    def test_record_success_stamps_last_verified(self):
        sid = self.reg.upsert("hn", "hn", integration_status=IntegrationStatus.ACTIVE_API,
                              status=SourceStatus.ACTIVE)
        self.reg.record_success(sid, verified_records=12)
        row = self.reg.get("hn")
        self.assertTrue(row["last_successful_at"])
        self.assertTrue(row["last_verified_at"])
        self.assertEqual(row["record_count"], 12)


if __name__ == "__main__":
    unittest.main()
