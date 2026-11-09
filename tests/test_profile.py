import unittest

from automatejob.profile import AnswerBank, CandidateProfile, UnverifiedField


class TestCandidateProfile(unittest.TestCase):
    def setUp(self):
        self.prof = CandidateProfile("data/candidate_profile.toml")

    def test_verified_name_from_resume(self):
        self.assertEqual(self.prof.name, "Sahil Gada")

    def test_verified_portfolio(self):
        self.assertEqual(self.prof.portfolio, "https://opsbysahil.site")

    def test_verified_experience_from_resume(self):
        self.assertTrue(self.prof.experience)
        self.assertIn("DevOps", self.prof.experience[0]["title"])

    def test_unverified_field_raises(self):
        # notice period is not in personal.toml -> verified() must raise.
        with self.assertRaises(UnverifiedField):
            self.prof.verified("notice_period")

    def test_unverified_fields_listed(self):
        missing = self.prof.unverified_fields()
        self.assertIn("notice_period", missing)
        self.assertIn("availability", missing)
        self.assertIn("work_authorization", missing)
        # verified facts are no longer in the checklist
        self.assertNotIn("name", missing)
        self.assertNotIn("education.summary", missing)
        self.assertNotIn("experience.entries", missing)

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
