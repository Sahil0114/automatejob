import unittest

from automatejob.config import AppConfig
from automatejob.models import Job
from automatejob.qualification import classify_job
from automatejob.storage import Storage


def _job(title="Junior Software Engineer", company="MixRank",
         location="Remote (Global)", freshness="fresh"):
    return Job(
        source_type="test", source_token="t", external_id="1",
        title=title, company=company, location=location,
        url="https://example.com/job/1", workplace_type="remote",
        description="Any (new grads ok). US citizenship/visa not required.",
        freshness=freshness,
    )


class TestFreshnessGate(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig.load("config.toml")

    def test_fresh_junior_is_actionable(self):
        j = _job()
        classify_job(j, self.cfg, None)
        self.assertEqual(j.actionable, 1)

    def test_stale_listing_not_actionable(self):
        j = _job(freshness="stale")
        classify_job(j, self.cfg, None)
        self.assertEqual(j.actionable, 0)

    def test_needs_verification_not_actionable(self):
        j = _job(freshness="needs_verification")
        classify_job(j, self.cfg, None)
        self.assertEqual(j.actionable, 0)


class TestFreshnessStorage(unittest.TestCase):
    def setUp(self):
        self.s = Storage(":memory:")
        j = _job()
        self.s.upsert_jobs([j])

    def tearDown(self):
        self.s.close()

    def test_set_freshness_by_url(self):
        n = self.s.set_freshness("https://example.com/job/1", "stale", "employer page shows 0")
        self.assertGreaterEqual(n, 1)
        row = self.s.conn.execute("SELECT freshness, freshness_note FROM jobs").fetchone()
        self.assertEqual(row["freshness"], "stale")
        self.assertEqual(row["freshness_note"], "employer page shows 0")

    def test_set_freshness_by_id(self):
        jid = self.s.conn.execute("SELECT id FROM jobs").fetchone()["id"]
        n = self.s.set_freshness(str(jid), "needs_verification")
        self.assertGreaterEqual(n, 1)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            self.s.set_freshness("https://example.com/job/1", "bogus")

    def test_source_refresh_inherits_manual_freshness_gate(self):
        self.s.set_freshness("https://example.com/job/1", "needs_verification")
        refreshed = _job()  # source adapter emits the normal default 'fresh'
        self.s.apply_stored_freshness(refreshed)
        self.assertEqual(refreshed.freshness, "needs_verification")

    def test_collector_does_not_reopen_manual_needs_verification_job(self):
        self.s.set_freshness("https://example.com/job/1", "needs_verification")
        incoming = _job()

        class Source:
            source_type = "test"
            token = "t"
            def fetch(self):
                return [incoming]

        from automatejob.collector import Collector
        collector = Collector(AppConfig.load("config.toml"), self.s, None)
        collector.build_sources = lambda: [Source()]
        collector.collect()
        row = self.s.conn.execute("SELECT freshness, actionable FROM jobs").fetchone()
        self.assertEqual(row["freshness"], "needs_verification")
        self.assertEqual(row["actionable"], 0)

    def test_rescore_preserves_freshness(self):
        # flag stale, then rescore: the stored freshness must survive
        self.s.set_freshness("https://example.com/job/1", "stale")
        j = _job()
        self.s.upsert_jobs([j])
        from automatejob.collector import Collector
        col = Collector(AppConfig.load("config.toml"), self.s, None)
        col.rescore()
        row = self.s.conn.execute("SELECT freshness, actionable FROM jobs").fetchone()
        self.assertEqual(row["freshness"], "stale")
        self.assertEqual(row["actionable"], 0)


if __name__ == "__main__":
    unittest.main()
