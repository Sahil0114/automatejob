import unittest

from automatejob.firecrawl import BudgetExceeded, FirecrawlBudget, FirecrawlConfig


class TestFirecrawlBudget(unittest.TestCase):
    def setUp(self):
        self.config = FirecrawlConfig(
            enabled=True, monthly_budget=1000, soft_limit=700,
            hard_limit=4, absolute_limit=1000, search_results_default=10,
        )
        self.budget = FirecrawlBudget(":memory:", self.config)

    def tearDown(self):
        self.budget.close()

    def test_standard_search_estimate_is_two_credits_per_ten_results(self):
        self.assertEqual(self.budget.estimate("search", results=1), 2)
        self.assertEqual(self.budget.estimate("search", results=10), 2)
        self.assertEqual(self.budget.estimate("search", results=11), 4)

    def test_hard_limit_blocks_request_before_execution(self):
        self.budget.record("search", estimated_credits=2)
        self.budget.record("scrape", estimated_credits=1)
        with self.assertRaises(BudgetExceeded):
            self.budget.reserve("map", estimated_credits=2)

    def test_provider_requires_explicit_cost(self):
        with self.assertRaises(ValueError):
            self.budget.estimate("alexandria", provider="blitzapi")

    def test_cache_and_usage_ledger_are_separate_from_authoritative_account_usage(self):
        self.budget.put_cache("search:junior", {"data": [1]})
        self.assertEqual(self.budget.get_cache("search:junior"), {"data": [1]})
        self.budget.record("search", provider="", estimated_credits=2)
        usage = self.budget.usage()
        self.assertEqual(usage["monthly_consumed"], 2)
        self.assertTrue(usage["estimated"])
        self.assertEqual(usage["by_endpoint"]["search"], 2)
class _Result:
    def __init__(self, code=0, stdout="{}", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class TestFirecrawlCLI(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def runner(args, **kwargs):
            self.calls.append(args)
            if args[1] == "status":
                return _Result(stdout="Authenticated\nCredits remaining: 999")
            if args[1] == "credit-usage":
                return _Result(stdout='{"remaining": 999, "used": 1}')
            return _Result(stdout='{"data":{"web":[{"url":"https://example.com/job"}]}}')
        from automatejob.firecrawl import FirecrawlCLI
        self.budget = FirecrawlBudget(":memory:", FirecrawlConfig(hard_limit=20))
        self.client = FirecrawlCLI(self.budget, runner=runner)

    def tearDown(self):
        self.budget.close()

    def test_search_normalizes_and_caches_response(self):
        first = self.client.search("junior devops Mumbai", limit=10)
        second = self.client.search("junior devops Mumbai", limit=10)
        self.assertEqual(first["data"]["web"][0]["url"], "https://example.com/job")
        self.assertTrue(second["cached"])
        self.assertEqual(sum(1 for c in self.calls if c[1] == "search"), 1)
        self.assertEqual(self.budget.usage()["monthly_consumed"], 2)

    def test_alexandria_without_declared_provider_cost_is_refused(self):
        with self.assertRaises(ValueError):
            self.client.alexandria("blitzapi", "jobs/search", {"query": "junior"})

    def test_account_usage_is_authoritative_when_cli_returns_json(self):
        usage = self.client.account_usage()
        self.assertEqual(usage["source"], "firecrawl_cli")
        self.assertEqual(usage["remaining"], 999)
    def test_failed_provider_call_is_conservatively_counted_as_unknown_cost(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "provider unavailable"
        from automatejob.firecrawl import FirecrawlCLI
        client = FirecrawlCLI(self.budget, runner=lambda *a, **k: Result())
        with self.assertRaises(RuntimeError):
            client.alexandria("blitzapi", "jobs/search", {"max_results": 1}, provider_cost=12)
        usage = self.budget.usage()
        self.assertEqual(usage["monthly_consumed"], 12)
        row = self.budget.conn.execute("SELECT status FROM firecrawl_operations").fetchone()
        self.assertEqual(row["status"], "unknown")
    def test_failure_with_explicit_zero_credits_does_not_consume_estimate(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "provider unavailable\nCredits: 0"
        from automatejob.firecrawl import FirecrawlCLI
        client = FirecrawlCLI(self.budget, runner=lambda *a, **k: Result())
        with self.assertRaises(RuntimeError):
            client.alexandria("blitzapi", "jobs/search", {"max_results": 1}, provider_cost=12)
        self.assertEqual(self.budget.usage()["monthly_consumed"], 0)
        row = self.budget.conn.execute("SELECT status,actual_credits FROM firecrawl_operations").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["actual_credits"], 0)


if __name__ == "__main__":
    unittest.main()
