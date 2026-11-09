import unittest

from automatejob.models import Job
from automatejob.storage import Storage


def make_job(title="DevOps Engineer", company="Vercel", url="https://e.com/1", **kw):
    defaults = dict(
        source_type="greenhouse", source_token="vercel", external_id="1",
        title=title, company=company, location="Remote", url=url,
        score=70.0, score_reasons=[{"factor": "role", "points": 20, "detail": "x"}],
    )
    defaults.update(kw)
    return Job(**defaults)


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")

    def tearDown(self):
        self.storage.close()

    def test_insert_and_dedup(self):
        a = make_job(external_id="1")
        new = self.storage.upsert_jobs([a])
        self.assertEqual(len(new), 1)
        self.assertEqual(self.storage.count(), 1)
        self.assertEqual(self.storage.count_new(), 1)

        # same normalized identity (different external id) -> deduplicated
        dup = make_job(external_id="2", title="devops engineer", company="Vercel, Inc.")
        new = self.storage.upsert_jobs([dup])
        self.assertEqual(len(new), 0)
        self.assertEqual(self.storage.count(), 1)

    def test_distinct_titles_are_separate(self):
        self.storage.upsert_jobs([make_job(title="DevOps Engineer")])
        self.storage.upsert_jobs([make_job(title="Backend Engineer", external_id="9")])
        self.assertEqual(self.storage.count(), 2)

    def test_list_and_mark_seen(self):
        self.storage.upsert_jobs([make_job(score=40.0),
                                  make_job(score=90.0, external_id="2", url="https://e.com/2")])
        rows = self.storage.list_jobs(only_new=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["score"], 90.0)  # highest first

        rows = self.storage.list_jobs(only_new=True, min_score=50.0)
        self.assertEqual(len(rows), 1)

        marked = self.storage.mark_seen()
        self.assertEqual(marked, 2)
        self.assertEqual(self.storage.count_new(), 0)
        self.assertEqual(len(self.storage.list_jobs(only_new=True)), 0)
        self.assertEqual(len(self.storage.list_jobs(only_new=False)), 2)

    def test_content_change_is_stamped(self):
        self.storage.upsert_jobs([make_job(description="old text")])
        # same posting re-seen with a changed description
        changed = make_job(description="new text")
        self.storage.upsert_jobs([changed])
        rows = self.storage.list_jobs(only_new=False)
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["changed_at"], "")
        self.assertEqual(rows[0]["content_hash"], changed.content_hash)

    def test_closure_requires_successive_absent_scans(self):
        a = make_job(external_id="1", url="https://e.com/1")
        b = make_job(external_id="2", url="https://e.com/2", title="Backend Engineer")
        self.storage.upsert_jobs([a, b])

        # Scan 1: only job A still seen -> B absent once, still open.
        self.storage.mark_absent("greenhouse", "vercel", {a.dedup_key}, close_after_scans=2)
        self.assertEqual(len(self.storage.list_jobs(only_new=False, status="closed")), 0)

        # Scan 2: B absent again -> now closed.
        self.storage.mark_absent("greenhouse", "vercel", {a.dedup_key}, close_after_scans=2)
        closed = self.storage.list_jobs(only_new=False, status="closed")
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["title"], "Backend Engineer")

    def test_reappearance_reopens(self):
        a = make_job(external_id="1", url="https://e.com/1")
        self.storage.upsert_jobs([a])
        self.storage.mark_absent("greenhouse", "vercel", set(), close_after_scans=2)
        self.storage.mark_absent("greenhouse", "vercel", set(), close_after_scans=2)
        self.assertEqual(len(self.storage.list_jobs(only_new=False, status="closed")), 1)

        # reappears -> reinserted as open again
        self.storage.upsert_jobs([a])
        self.assertEqual(len(self.storage.list_jobs(only_new=False, status="closed")), 0)
        self.assertEqual(len(self.storage.list_jobs(only_new=False, status="open")), 1)


if __name__ == "__main__":
    unittest.main()
