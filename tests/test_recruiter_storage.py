import unittest

from automatejob.outreach import OutreachStorage
from automatejob.recruiter import RecruiterPost, RecruiterRole


class TestRecruiterPostStorage(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")

    def tearDown(self):
        self.s.close()

    def test_stores_one_post_with_multiple_roles_and_published_emails(self):
        post = RecruiterPost(
            original_url="https://example.com/post", company="Acme", recruiter_name="Priya",
            recruiter_title="Technical Recruiter", published_at="2026-11-11",
            text="Hiring now", roles=[
                RecruiterRole("Junior DevOps Engineer", location="Mumbai"),
                RecruiterRole("Backend Engineer", location="Remote India"),
            ], published_emails=["jobs@acme.com", "talent@acme.com"], confidence=.9,
        )
        post_id = self.s.upsert_recruiter_post(post)
        again = self.s.upsert_recruiter_post(post)
        self.assertEqual(post_id, again)
        rows = self.s.list_recruiter_posts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["roles"]), 2)
        self.assertEqual(rows[0]["published_emails"], ["jobs@acme.com", "talent@acme.com"])


if __name__ == "__main__":
    unittest.main()
