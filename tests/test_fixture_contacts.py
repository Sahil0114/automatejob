import unittest

from automatejob.outreach import OutreachStorage


class TestFixtureContacts(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.fixture_id = self.s.upsert_contact("c1", "Jane Fixture", "CTO", "cto",
                                                email="jane@acme.com")
        self.real_id = self.s.upsert_contact("c1", "Real Person", "Founder", "founder",
                                             email="real@acme.com", source_url="https://acme.com/team")

    def tearDown(self):
        self.s.close()

    def test_fixture_contact_is_hidden_from_default_production_list(self):
        self.assertTrue(self.s.mark_contact_fixture(self.fixture_id, "local smoke-test contact"))
        names = [c["full_name"] for c in self.s.list_contacts_joined()]
        self.assertNotIn("Jane Fixture", names)
        self.assertIn("Real Person", names)

        all_names = [c["full_name"] for c in self.s.list_contacts_joined(include_fixtures=True)]
        self.assertIn("Jane Fixture", all_names)


if __name__ == "__main__":
    unittest.main()
