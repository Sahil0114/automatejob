import unittest

from automatejob.ats_detect import detect_ats
from automatejob.company import normalize_domain


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def close(self):
        pass


class FakeHttp:
    def __init__(self, status_by_url):
        self.status_by_url = status_by_url
        self.calls = []

    def get(self, url, timeout=None, stream=True):
        self.calls.append(url)
        status = self.status_by_url.get(url, 404)
        return FakeResponse(status)


class TestAtsDetect(unittest.TestCase):
    def test_careers_url_pattern_is_used_first(self):
        http = FakeHttp({
            "https://api.ashbyhq.com/posting-api/job-board/kong?includeCompensation=true": 200,
        })
        ats, token, url = detect_ats("Kong", "konghq.com",
                                     "https://jobs.ashbyhq.com/kong", http=http)
        self.assertEqual(ats, "ashby")
        self.assertEqual(token, "kong")

    def test_inferred_token_must_validate(self):
        # Only the greenhouse endpoint returns 200 for the candidate token.
        http = FakeHttp({
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": 200,
            "https://api.lever.co/v0/postings/acme?mode=json": 404,
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true": 404,
        })
        ats, token, _ = detect_ats("Acme", "acme.com", "", http=http)
        self.assertEqual(ats, "greenhouse")
        self.assertEqual(token, "acme")

    def test_unverified_ats_returns_empty(self):
        http = FakeHttp({})  # everything 404
        ats, token, _ = detect_ats("Nobody", "nobody.example", "", http=http)
        self.assertEqual(ats, "")
        self.assertEqual(token, "")

    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("https://www.foo.com/x"), "foo.com")


if __name__ == "__main__":
    unittest.main()
