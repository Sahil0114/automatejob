import io
import json
import unittest

from automatejob.dashboard import Dashboard
from automatejob.outreach import OutreachStorage
from automatejob.profile import CandidateProfile


class _W:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data


class _Handler:
    def __init__(self, payload):
        data = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(data))}
        self.rfile = io.BytesIO(data)
        self.wfile = _W()
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass


class TestDashboardContactWorkflow(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")
        self.s.upsert_company("mixrank", "MixRank", "mixrank.com", "active_vacancy", 70, [])
        self.s.add_evidence("mixrank", "hiring", "New grads ok", "https://example.com/job")
        self.dashboard = Dashboard(self.s, profile=CandidateProfile("data/candidate_profile.toml"))
        self.payload = {
            "company_id": "mixrank", "full_name": "Scott Milliken", "title": "Founder",
            "role_kind": "founder", "email": "scott@mixrank.com",
            "source_url": "https://example.com/team",
        }

    def tearDown(self):
        self.s.close()

    def test_saving_contact_creates_persisted_reviewable_draft(self):
        h = _Handler(self.payload)
        self.dashboard._add_contact(h)
        response = json.loads(h.wfile.data)
        self.assertEqual(h.status, 200)
        self.assertIn("draft_id", response)
        self.assertEqual(len(self.s.list_drafts()), 1)
        self.assertEqual(self.s.list_drafts()[0]["state"], "draft_ready")

    def test_repeat_contact_save_does_not_duplicate_same_draft(self):
        self.dashboard._add_contact(_Handler(self.payload))
        h = _Handler(self.payload)
        self.dashboard._add_contact(h)
        self.assertEqual(len(self.s.list_drafts()), 1)
        self.assertTrue(json.loads(h.wfile.data)["draft_already_exists"])


if __name__ == "__main__":
    unittest.main()
