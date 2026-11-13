import unittest

from automatejob.recruiter import parse_recruiter_post


class TestRecruiterPostExpansion(unittest.TestCase):
    def test_application_email_requires_application_action_phrase(self):
        post = parse_recruiter_post(
            "Founder is great. Email: hello@acme.com for questions.\n"
            "- Junior DevOps Engineer\n"
            "Apply to: hiring@acme.com",
            original_url="https://example.com/post")
        # "hello@acme.com" has no application-action phrase nearby; only the
        # explicit "Apply to" contact is treated as an application email.
        self.assertEqual(post.published_emails, ["hiring@acme.com"])

    def test_multiple_application_emails_in_one_apply_line(self):
        post = parse_recruiter_post(
            "- Backend Engineer\n"
            "Apply: hiring@acme.com or careers@acme.com",
            original_url="https://example.com/post")
        self.assertEqual(post.published_emails, ["hiring@acme.com", "careers@acme.com"])

    def test_application_urls_extracted(self):
        post = parse_recruiter_post(
            "- SRE\nApply at https://jobs.example.com/sre-123",
            original_url="https://example.com/post")
        self.assertEqual(post.application_urls, ["https://jobs.example.com/sre-123"])

    def test_roles_get_first_email_and_url(self):
        post = parse_recruiter_post(
            "- DevOps Engineer\n"
            "- Cloud Engineer\n"
            "Apply: hiring@acme.com via https://jobs.acme.com",
            original_url="https://example.com/post")
        self.assertTrue(all(r.published_email == "hiring@acme.com" for r in post.roles))
        self.assertTrue(all(r.application_url == "https://jobs.acme.com" for r in post.roles))

    def test_no_fabricated_recruiter_or_company(self):
        post = parse_recruiter_post(
            "Some prose without bullets.\nApply: hiring@acme.com",
            original_url="https://example.com/post")
        self.assertEqual(post.company, "")
        self.assertEqual(post.recruiter_name, "")
        self.assertEqual(post.roles, [])


if __name__ == "__main__":
    unittest.main()
