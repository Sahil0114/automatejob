import unittest

from automatejob.discovery_log import DiscoveryLog
from automatejob.company import CompanyStorage
from automatejob.sources.company_sources import YCDirectorySource


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, timeout=20):
        self.calls.append(url)
        page = self.pages.pop(0)
        return FakeResponse(page)


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


def _page(companies, next_page=""):
    return {"companies": companies, "nextPage": next_page}


def _company(name, domain):
    return {
        "name": name, "website": f"https://{domain}", "oneLiner": "x",
        "longDescription": "", "locations": [], "regions": ["India"],
        "tags": ["AI"], "industries": [], "teamSize": 5, "batch": "W21",
        "url": f"https://www.ycombinator.com/companies/{name}",
    }


class TestDiscoveryLog(unittest.TestCase):
    def test_start_complete_and_summary(self):
        log = DiscoveryLog(":memory:")
        try:
            op_id = log.start("hn", access_method="api", query="who is hiring")
            log.complete(op_id, returned_records=30, new_jobs=3)
            s = log.summary()
            self.assertEqual(s["total"], 1)
            self.assertEqual(s["by_outcome"]["success"]["count"], 1)
        finally:
            log.close()

    def test_failure_is_not_success(self):
        log = DiscoveryLog(":memory:")
        try:
            op_id = log.start("x", query="post search")
            log.complete(op_id, outcome="failure", error_reason="blocked")
            s = log.summary()
            self.assertEqual(s["by_outcome"]["failure"]["count"], 1)
            self.assertNotIn("success", s["by_outcome"])
        finally:
            log.close()


class TestYCPaginationResume(unittest.TestCase):
    def test_iter_pages_yields_next_url_and_resumes(self):
        p1 = _page([_company("Alpha", "alpha.com")], "https://api.ycombinator.com/v0.1/companies?page=2")
        p2 = _page([_company("Beta", "beta.com")], "")
        src = YCDirectorySource(http=FakeHttp([p1, p2]))
        pages = list(src.iter_pages())
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][1], "https://api.ycombinator.com/v0.1/companies?page=2")
        self.assertEqual(pages[1][1], "")

    def test_resume_from_checkpoint_url(self):
        p2 = _page([_company("Beta", "beta.com")], "")
        src = YCDirectorySource(http=FakeHttp([p2]),
                                start_url="https://api.ycombinator.com/v0.1/companies?page=2")
        pages = list(src.iter_pages())
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0][0][0].name, "Beta")


class TestCompanyCheckpoints(unittest.TestCase):
    def test_checkpoint_roundtrip(self):
        store = CompanyStorage(":memory:")
        try:
            self.assertEqual(store.get_checkpoint("yc_directory_next_page"), "")
            store.set_checkpoint("yc_directory_next_page", "https://.../page=3")
            self.assertEqual(store.get_checkpoint("yc_directory_next_page"),
                             "https://.../page=3")
            store.clear_checkpoint("yc_directory_next_page")
            self.assertEqual(store.get_checkpoint("yc_directory_next_page"), "")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
