import unittest

from automatejob.models import (
    Job,
    make_content_hash,
    make_cross_board_key,
    make_dedup_key,
    normalize_company,
    normalize_title,
    normalize_url,
)


class TestNormalization(unittest.TestCase):
    def test_normalize_company_strips_suffixes_and_case(self):
        self.assertEqual(normalize_company("Cloudflare, Inc."), "cloudflare")
        self.assertEqual(normalize_company("Databricks"), "databricks")
        self.assertEqual(normalize_company("  MongoDB, Inc.  "), "mongodb")
        self.assertEqual(normalize_company(""), "")

    def test_normalize_title_collapses_and_lowercases(self):
        self.assertEqual(normalize_title("DevOps  Engineer"), "devops engineer")
        self.assertEqual(normalize_title("  Senior Software Engineer, Backend  "),
                         "senior software engineer backend")

    def test_normalize_url_drops_tracking_but_keeps_ids(self):
        # utm params dropped
        self.assertEqual(
            normalize_url("https://Boards.Greenhouse.IO/Vercel/jobs/123?utm_source=1"),
            "https://boards.greenhouse.io/vercel/jobs/123",
        )
        # posting id in query string MUST be preserved (Databricks style)
        self.assertEqual(
            normalize_url("https://databricks.com/job?gh_jid=87395&ref=home"),
            "https://databricks.com/job?gh_jid=87395",
        )

    def test_dedup_key_identical_for_equivalent_jobs(self):
        a = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel, Inc.",
                "Remote - India", "https://jobs.example.com/a?utm_source=x")
        b = Job("greenhouse", "x", "2", "devops engineer", "Vercel",
                "Mumbai", "https://jobs.example.com/a")
        self.assertEqual(a.dedup_key, b.dedup_key)

    def test_dedup_key_differs_on_title(self):
        a = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel",
                "Remote", "https://jobs.example.com/a")
        b = Job("greenhouse", "x", "2", "Backend Engineer", "Vercel",
                "Remote", "https://jobs.example.com/a")
        self.assertNotEqual(a.dedup_key, b.dedup_key)

    def test_cross_board_key_ignores_source_url(self):
        # Same role on two different boards -> same cross-board key,
        # different per-source dedup key.
        a = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel",
                "Remote", "https://boards.greenhouse.io/vercel/jobs/1")
        b = Job("lever", "vercel", "2", "DevOps Engineer", "Vercel",
                "Remote", "https://jobs.lever.co/vercel/2")
        self.assertEqual(a.cross_board_key, b.cross_board_key)
        self.assertNotEqual(a.dedup_key, b.dedup_key)

    def test_cross_board_key_differs_on_location(self):
        a = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel",
                "Mumbai", "https://a")
        b = Job("greenhouse", "x", "2", "DevOps Engineer", "Vercel",
                "Bengaluru", "https://b")
        self.assertNotEqual(a.cross_board_key, b.cross_board_key)

    def test_content_hash_detects_changes(self):
        a = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel",
                "Remote", "https://a", description="old text")
        b = Job("greenhouse", "x", "1", "DevOps Engineer", "Vercel",
                "Remote", "https://a", description="new text")
        self.assertEqual(a.dedup_key, b.dedup_key)
        self.assertNotEqual(a.content_hash, b.content_hash)


if __name__ == "__main__":
    unittest.main()
