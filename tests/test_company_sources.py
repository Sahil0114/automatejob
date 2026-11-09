import unittest

from automatejob.sources.company_sources import (
    GITHUB_API, YC_API, GitHubOrgSource, YCDirectorySource,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, timeout=None, stream=None):
        self.calls.append(url)
        status, payload = self.payloads[url]
        return FakeResponse(status, payload)


YC_PAGE = {
    "companies": [
        {
            "id": 1, "name": "MumbaiPay", "slug": "mumbaipay",
            "website": "https://mumbaipay.com", "oneLiner": "Payments for India",
            "longDescription": "UPI payments infrastructure.", "teamSize": 12,
            "url": "https://www.ycombinator.com/companies/mumbaipay",
            "batch": "W24", "tags": ["Fintech"], "status": "Active",
            "industries": ["B2B"], "regions": ["India", "Asia"],
            "locations": ["Mumbai"],
        },
        {
            "id": 2, "name": "RemoteAI", "slug": "remoteai",
            "website": "https://remoteai.dev", "oneLiner": "AI agents, anywhere",
            "longDescription": "Remote-first AI infra.", "teamSize": 5,
            "url": "https://www.ycombinator.com/companies/remoteai",
            "batch": "S23", "tags": ["AI"], "status": "Active",
            "industries": ["B2B"], "regions": ["Remote", "Global"],
            "locations": ["Remote / Global"],
        },
    ],
    "nextPage": "", "page": 1, "totalPages": 1,
}


class TestYCDirectorySource(unittest.TestCase):
    def test_map_mumbai_and_remote(self):
        http = FakeHttp({YC_API: (200, YC_PAGE)})
        src = YCDirectorySource(http=http, delay=0)
        companies = src.fetch()
        self.assertEqual(len(companies), 2)
        mumbai = companies[0]
        self.assertEqual(mumbai.name, "MumbaiPay")
        self.assertTrue(mumbai.mumbai_office)
        self.assertEqual(mumbai.remote_policy, "unknown")  # onsite Mumbai, not remote
        self.assertEqual(mumbai.funding_stage, "YC W24")

        remote = companies[1]
        self.assertEqual(remote.remote_policy, "worldwide_remote")
        self.assertIsNone(remote.mumbai_office)


class TestGitHubOrgSource(unittest.TestCase):
    def test_enrich_technologies(self):
        http = FakeHttp({
            f"{GITHUB_API}/orgs/acme": (200, {
                "login": "acme", "name": "Acme Inc", "blog": "https://acme.com",
                "description": "Infra tools", "location": "Mumbai",
                "html_url": "https://github.com/acme",
            }),
            f"{GITHUB_API}/orgs/acme/repos?per_page=30&sort=pushed": (200, [
                {"name": "k8s-operator", "description": "Kubernetes operator in Go",
                 "language": "Go"},
                {"name": "terraform-modules", "description": "", "language": "HCL"},
            ]),
        })
        src = GitHubOrgSource(["acme"], http=http, delay=0)
        companies = src.fetch()
        self.assertEqual(len(companies), 1)
        c = companies[0]
        self.assertEqual(c.name, "Acme Inc")
        self.assertIn("Go", c.technologies)
        self.assertIn("Kubernetes", c.technologies)

    def test_org_failure_is_skipped(self):
        http = FakeHttp({f"{GITHUB_API}/orgs/nope": (404, {})})
        src = GitHubOrgSource(["nope"], http=http, delay=0)
        self.assertEqual(src.fetch(), [])


if __name__ == "__main__":
    unittest.main()
