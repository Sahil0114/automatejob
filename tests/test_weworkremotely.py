import unittest

from automatejob.sources.weworkremotely import WeWorkRemotelySource


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeHttp:
    def __init__(self, text):
        self.text = text

    def get(self, url, timeout=30):
        return FakeResponse(self.text)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>We Work Remotely</title>
<item>
  <title>Acme Corp: Senior DevOps Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/acme-1</link>
  <description>&lt;p&gt;Remote DevOps role (worldwide).&lt;/p&gt;</description>
  <pubDate>Mon, 10 Nov 2026 10:00:00 +0000</pubDate>
</item>
<item>
  <title>Backend Engineer at Globex</title>
  <link>https://weworkremotely.com/remote-jobs/globex-2</link>
  <description>Remote backend role.</description>
  <pubDate>Tue, 11 Nov 2026 10:00:00 +0000</pubDate>
</item>
</channel></rss>
"""


class TestWeWorkRemotely(unittest.TestCase):
    def test_parses_items_and_splits_company_role(self):
        src = WeWorkRemotelySource("remote-programming-jobs", http=FakeHttp(RSS))
        jobs = src.fetch()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].company, "Acme Corp")
        self.assertEqual(jobs[0].title, "Senior DevOps Engineer")
        self.assertEqual(jobs[0].workplace_type, "remote")
        self.assertEqual(jobs[1].company, "Globex")
        self.assertEqual(jobs[1].title, "Backend Engineer")
        self.assertTrue(jobs[0].posted_at)

    def test_source_type(self):
        self.assertEqual(WeWorkRemotelySource.source_type, "weworkremotely")


if __name__ == "__main__":
    unittest.main()
