import unittest

from automatejob.profile import AnswerBank, CandidateProfile, UnverifiedField


class TestCandidateProfile(unittest.TestCase):
    def setUp(self):
        self.prof = CandidateProfile("data/candidate_profile.toml")

    def test_verified_portfolio(self):
        self.assertEqual(self.prof.portfolio, "https://opsbysahil.site")

    def test_unverified_field_raises(self):
        with self.assertRaises(UnverifiedField):
            self.prof.verified("name")

    def test_unverified_fields_listed(self):
        missing = self.prof.unverified_fields()
        self.assertIn("education.summary", missing)
        self.assertIn("employment.work_authorization", missing)

    def test_verified_technologies_present(self):
        self.assertTrue(self.prof.technologies)
        self.assertIn("Go", self.prof.technologies)


class TestAnswerBank(unittest.TestCase):
    def test_questions_and_templates(self):
        bank = AnswerBank("data/answer_bank.toml")
        self.assertIn("why_role", bank.questions())
        self.assertIn("{{role_label}}", bank.template("why_role"))


if __name__ == "__main__":
    unittest.main()
