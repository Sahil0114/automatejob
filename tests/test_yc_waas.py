import json
import unittest

from automatejob.config import AppConfig
from automatejob.qualification import classify_job
from automatejob.sources.yc_waas import YCJobsSource, _extract_jobs


def _page_html(company: dict, jobs: list) -> str:
    data = {"component": "CompanyV2Page", "props": {"company": company}}
    import html as h
    return f'<div data-page="{h.escape(json.dumps(data))}"></div>'


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeHttp:
    def __init__(self, html):
        self.html = html
        self.headers = {}

    def get(self, url, headers=None, timeout=None):
        return _FakeResp(self.html)


class TestYCJobsSource(unittest.TestCase):
    COMPANY = {
        "name": "MixRank",
        "slug": "mixrank",
        "description": "Data products for sales, marketing and finance.",
        "jobs": [
            {
                "id": 72514,
                "title": "Junior Software Engineer - Remote (Global), Full-Time",
                "location": "Remote",
                "jobType": "Full-time",
                "roleType": "Full stack",
                "salary": "$12K - $24K",
                "minExperience": "Any (new grads ok)",
                "sponsorsVisa": "US citizenship/visa not required",
                "applyUrl": "https://www.workatastartup.com/jobs/72514",
            },
            {
                "id": 68661,
                "title": "Software Engineer - Global (Remote)",
                "location": "Remote",
                "jobType": "Full-time",
                "minExperience": "3+ years",
                "sponsorsVisa": "US citizenship/visa not required",
                "applyUrl": "https://www.workatastartup.com/jobs/68661",
            },
            {
                "id": 94931,
                "title": "Junior Software Engineer \u2014 Remote, Russia",
                "location": "RU / Remote (RU)",
                "jobType": "Full-time",
                "minExperience": "Any (new grads ok)",
                "sponsorsVisa": "US citizenship/visa not required",
                "applyUrl": "",
            },
        ],
    }

    def test_extract_jobs_parses_data_page(self):
        company, jobs = _extract_jobs(_page_html(self.COMPANY, self.COMPANY["jobs"]))
        self.assertEqual(company["name"], "MixRank")
        self.assertEqual(len(jobs), 3)

    def test_global_remote_junior_is_actionable(self):
        cfg = AppConfig.load("config.toml")
        src = YCJobsSource(token="mixrank", name="MixRank",
                           http=_FakeHttp(_page_html(self.COMPANY, self.COMPANY["jobs"])))
        jobs = src.fetch()
        by_id = {int(j.external_id): j for j in jobs}
        junior = by_id[72514]
        # "Global" is surfaced from the title into the location field.
        self.assertEqual(junior.location, "Remote (Global)")
        self.assertIn("global", junior.location.lower())
        classify_job(junior, cfg, None)
        self.assertEqual(junior.eligibility, "confirmed")
        self.assertEqual(junior.seniority, "junior")
        self.assertEqual(junior.role_family, "software_engineering")
        self.assertEqual(junior.actionable, 1)

    def test_three_years_experience_is_not_junior(self):
        cfg = AppConfig.load("config.toml")
        src = YCJobsSource(token="mixrank", name="MixRank",
                           http=_FakeHttp(_page_html(self.COMPANY, self.COMPANY["jobs"])))
        jobs = src.fetch()
        mid = next(j for j in jobs if j.external_id == "68661")
        classify_job(mid, cfg, None)
        self.assertNotEqual(mid.seniority, "junior")

    def test_country_restricted_remote_not_confirmed(self):
        cfg = AppConfig.load("config.toml")
        src = YCJobsSource(token="mixrank", name="MixRank",
                           http=_FakeHttp(_page_html(self.COMPANY, self.COMPANY["jobs"])))
        jobs = src.fetch()
        ru = next(j for j in jobs if j.external_id == "94931")
        classify_job(ru, cfg, None)
        self.assertNotEqual(ru.eligibility, "confirmed")
        self.assertEqual(ru.actionable, 0)


if __name__ == "__main__":
    unittest.main()
