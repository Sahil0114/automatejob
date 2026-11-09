import unittest

from automatejob.sources.hn import HNSource
from automatejob.sources.simplifyjobs import SimplifyJobsSource


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeHttp:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, headers=None, timeout=None):
        return _FakeResp(self.mapping[url])


class TestHNSource(unittest.TestCase):
    def setUp(self):
        self.src = HNSource(token="latest", name="HN", http=None)

    def test_parse_standard_comment(self):
        comment = {
            "id": "123",
            "created_at": "2026-11-02T00:00:00Z",
            "text": "Simple Analytics | DevOps Engineer | REMOTE (worldwide, any timezone) | "
                    "Full-time<p>Hi HN, we are hiring.",
        }
        job = self.src._parse_comment(comment)
        self.assertEqual(job.company, "Simple Analytics")
        self.assertEqual(job.title, "DevOps Engineer")
        self.assertIn("worldwide", job.location)
        self.assertEqual(job.workplace_type, "remote")
        self.assertEqual(job.source_token, "latest")
        self.assertIn("news.ycombinator.com/item?id=123", job.url)

    def test_parse_us_only_remote(self):
        comment = {"id": "1", "text": "Acme | Eng | REMOTE (US) | Full-time"}
        job = self.src._parse_comment(comment)
        self.assertIn("US", job.location)
        self.assertEqual(job.workplace_type, "remote")

    def test_parse_onsite(self):
        comment = {"id": "2", "text": "Acme | Eng | Austin, TX | ONSITE"}
        job = self.src._parse_comment(comment)
        self.assertEqual(job.workplace_type, "onsite")
        self.assertIn("Austin", job.location)

    def test_empty_comment_returns_none(self):
        self.assertIsNone(self.src._parse_comment({"id": "3", "text": ""}))
        self.assertIsNone(self.src._parse_comment({"id": "4", "text": "NoPipeHere"}))


class TestSimplifyJobsSource(unittest.TestCase):
    SAMPLE = """<html><body><table><thead><tr><th>Company</th><th>Role</th>
<th>Location</th><th>Application</th><th>Age</th></tr></thead><tbody>
<tr><td><strong><a href="https://simplify.jobs/c/Acme?utm=x">Acme Corp</a></strong></td>
<td>Software Engineer Intern</td><td>Remote<br>Worldwide</td>
<td><div><a href="https://acme.com/careers/123"><img alt="Apply"></a>
<a href="https://simplify.jobs/p/abc"><img alt="Simplify"></a></div></td><td>0d</td></tr>
<tr><td>↳</td><td>DevOps Intern</td><td>Remote</td>
<td><div><a href="https://acme.com/careers/456"><img alt="Apply"></a></div></td><td>1d</td></tr>
</tbody></table></body></html>"""

    def test_fetch_parses_rows_and_keeps_employer_link(self):
        import automatejob.sources.simplifyjobs as sj
        http = _FakeHttp({sj._RAW_URL.format(token="MyInternships"): self.SAMPLE})
        src = SimplifyJobsSource(token="MyInternships", name="MyInternships", http=http)
        jobs = src.fetch()
        self.assertEqual(len(jobs), 2)
        first = jobs[0]
        self.assertEqual(first.company, "Acme Corp")
        self.assertEqual(first.title, "Software Engineer Intern")
        self.assertEqual(first.employment_type, "Internship")
        # employer link retained, simplify wrapper dropped
        self.assertEqual(first.apply_url, "https://acme.com/careers/123")
        self.assertNotIn("simplify.jobs", first.apply_url)
        # sub-role inherits company name
        self.assertEqual(jobs[1].company, "Acme Corp")
        self.assertEqual(jobs[1].title, "DevOps Intern")

    def test_http_error_raises(self):
        from automatejob.sources.base import SourceError
        http = _FakeHttp({})
        http.get = lambda *a, **k: _FakeResp("", status_code=404)
        src = SimplifyJobsSource(token="t", name="t", http=http)
        with self.assertRaises(SourceError):
            src.fetch()


if __name__ == "__main__":
    unittest.main()
