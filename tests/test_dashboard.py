import json
import unittest

from automatejob.dashboard import Dashboard
from automatejob.outreach import OutreachStorage


class FakeHandler:
    def __init__(self, path="/", headers=None):
        self.path = path
        self.headers = headers or {}
        self.status = None
        self.payload = None
        self.command = "GET"

    def send_response(self, status):
        self.status = status

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


class _W:
    def write(self, b):
        self.data = b


class TestDashboardAccess(unittest.TestCase):
    def setUp(self):
        self.outreach = OutreachStorage(":memory:")
        self.outreach.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])

    def tearDown(self):
        self.outreach.close()

    def test_unauthorized_without_token(self):
        dash = Dashboard(self.outreach, token="secret")
        self.assertFalse(dash._authorized(FakeHandler(path="/api/overview")))
        self.assertFalse(dash._authorized(
            FakeHandler(path="/api/overview", headers={"Authorization": "Bearer wrong"})))

    def test_authorized_with_token(self):
        dash = Dashboard(self.outreach, token="secret")
        self.assertTrue(dash._authorized(
            FakeHandler(path="/api/overview", headers={"Authorization": "Bearer secret"})))
        self.assertTrue(dash._authorized(
            FakeHandler(path="/api/overview?token=secret")))

    def test_no_token_means_open(self):
        dash = Dashboard(self.outreach, token="")
        self.assertTrue(dash._authorized(FakeHandler()))

    def test_route_returns_401(self):
        dash = Dashboard(self.outreach, token="secret")
        h = FakeHandler(path="/api/overview")
        h.wfile = _W()
        dash.route(h)
        self.assertEqual(h.status, 401)


if __name__ == "__main__":
    unittest.main()
