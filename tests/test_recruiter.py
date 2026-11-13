import unittest

from automatejob.recruiter import parse_recruiter_post


class TestRecruiterPostExtraction(unittest.TestCase):
    def test_extracts_multiple_bulleted_roles_and_published_professional_emails(self):
        post = parse_recruiter_post(
            "We're hiring in Mumbai!\n"
            "- Junior DevOps Engineer (0-2 years)\n"
            "- Python Backend Engineer\n"
            "Apply: hiring@acme.com or careers@acme.com\n",
            original_url="https://example.com/post", company="Acme", published_at="2026-11-11")
        self.assertEqual([r.title for r in post.roles],
                         ["Junior DevOps Engineer", "Python Backend Engineer"])
        self.assertEqual(post.published_emails, ["hiring@acme.com", "careers@acme.com"])
        self.assertEqual(post.published_at, "2026-11-11")

    def test_does_not_promote_invalid_addresses(self):
        post = parse_recruiter_post("Contact hiring@ or hello@example", original_url="https://example.com")
        self.assertEqual(post.published_emails, [])


if __name__ == "__main__":
    unittest.main()
