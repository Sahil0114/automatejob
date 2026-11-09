import unittest

from automatejob.sources import AshbySource, GreenhouseSource, LeverSource
from automatejob.sources.base import SourceError


class FakeResponse:
    def __init__(self, status_code, payload, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("invalid JSON")
        return self._payload


class FakeHttp:
    def __init__(self, payloads):
        self.payloads = payloads  # url -> (status, payload) or (status, payload, raise_json)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        entry = self.payloads[url]
        if len(entry) == 3:
            return FakeResponse(entry[0], entry[1], raise_json=entry[2])
        return FakeResponse(entry[0], entry[1])


GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/vercel/jobs?content=true"

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 111,
            "title": "DevOps Engineer",
            "company_name": "Vercel",
            "location": {"name": "Remote - India"},
            "absolute_url": "https://job-boards.greenhouse.io/vercel/jobs/111",
            "first_published": "2026-01-01T00:00:00Z",
            "content": "<p>Build <b>Kubernetes</b> and infra. &amp;lt;b&amp;gt;CI/CD&amp;lt;/b&amp;gt;</p>",
            "metadata": [{"name": "Career Site Categories", "value": "Engineering"}],
        },
        {
            "id": 222,
            "title": "Account Executive",
            "company_name": None,
            "location": {"name": "New York, NY"},
            "absolute_url": "https://job-boards.greenhouse.io/vercel/jobs/222",
            "first_published": None,
            "content": None,  # malformed/missing description must not crash
            "metadata": [],
        },
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "abc-123",
        "text": "Backend Engineer (Go)",
        "categories": {"commitment": "Full-time", "location": "Mumbai, India",
                       "team": "Engineering", "allLocations": ["Mumbai, India"]},
        "workplaceType": "hybrid",
        "createdAt": 1700000000000,
        "hostedUrl": "https://jobs.lever.co/spotify/abc-123",
        "applyUrl": "https://jobs.lever.co/spotify/abc-123/apply",
        "descriptionPlain": "Build distributed systems in Go.",
    }
]

ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/kong?includeCompensation=true"

ASHBY_PAYLOAD = {
    "jobs": [
        {
            "title": "Software Engineer 2, Managed Gateways",
            "location": "Bangalore, India",
            "secondaryLocations": [{"location": "Mumbai, India"}],
            "workplaceType": "Hybrid",
            "employmentType": "FullTime",
            "descriptionHtml": "<p>Work on <strong>API gateways</strong> in Go.</p>",
            "publishedAt": "2026-05-01T00:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/kong/abc123",
            "applyUrl": "https://jobs.ashbyhq.com/kong/abc123/application",
            "isListed": True,
        },
        {
            "title": "Internal (unlisted) Role",
            "location": "San Francisco",
            "workplaceType": "OnSite",
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/kong/secret",
            "applyUrl": "",
            "isListed": False,
        },
    ]
}


class TestGreenhouseSource(unittest.TestCase):
    def test_parse_with_content(self):
        http = FakeHttp({GREENHOUSE_URL: (200, GREENHOUSE_PAYLOAD)})
        jobs = GreenhouseSource(token="vercel", name="Vercel", http=http).fetch()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].title, "DevOps Engineer")
        self.assertEqual(jobs[0].company, "Vercel")
        self.assertEqual(jobs[0].workplace_type, "remote")
        # HTML content converted to searchable text
        self.assertIn("Kubernetes", jobs[0].description)
        self.assertIn("CI/CD", jobs[0].description)
        # fallback company when company_name is null
        self.assertEqual(jobs[1].company, "Vercel")
        self.assertEqual(jobs[1].workplace_type, "")
        # malformed (None) description tolerated
        self.assertEqual(jobs[1].description, "")

    def test_http_error_raises_source_error(self):
        http = FakeHttp({"https://boards-api.greenhouse.io/v1/boards/x/jobs?content=true": (403, {})})
        with self.assertRaises(SourceError):
            GreenhouseSource(token="x", http=http).fetch()

    def test_invalid_json_raises_source_error(self):
        http = FakeHttp({GREENHOUSE_URL: (200, "not json", True)})
        with self.assertRaises(SourceError):
            GreenhouseSource(token="vercel", http=http).fetch()


class TestLeverSource(unittest.TestCase):
    def test_parse(self):
        http = FakeHttp({"https://api.lever.co/v0/postings/spotify?mode=json":
                         (200, LEVER_PAYLOAD)})
        jobs = LeverSource(token="spotify", name="Spotify", http=http).fetch()
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.title, "Backend Engineer (Go)")
        self.assertEqual(j.company, "Spotify")
        self.assertEqual(j.location, "Mumbai, India")
        self.assertEqual(j.workplace_type, "hybrid")
        self.assertEqual(j.employment_type, "Full-time")
        self.assertIn("distributed systems", j.description)
        self.assertTrue(j.posted_at.startswith("2023-11-14"))  # ms -> ISO


class TestAshbySource(unittest.TestCase):
    def test_parse(self):
        http = FakeHttp({ASHBY_URL: (200, ASHBY_PAYLOAD)})
        jobs = AshbySource(token="kong", name="Kong", http=http).fetch()
        # unlisted posting skipped
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.title, "Software Engineer 2, Managed Gateways")
        self.assertEqual(j.location, "Bangalore, India")
        self.assertEqual(j.workplace_type, "hybrid")
        self.assertEqual(j.employment_type, "Full-time")
        self.assertEqual(j.secondary_locations, ["Mumbai, India"])
        self.assertIn("API gateways", j.description)
        self.assertTrue(j.url.startswith("https://jobs.ashbyhq.com/kong/"))

    def test_unknown_board_is_source_error(self):
        http = FakeHttp({ASHBY_URL: (404, "Not Found")})
        with self.assertRaises(SourceError):
            AshbySource(token="kong", http=http).fetch()


if __name__ == "__main__":
    unittest.main()
