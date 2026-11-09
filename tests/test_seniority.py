import unittest

from automatejob.models import Job
from automatejob.seniority import (
    INTERMEDIATE, INTERNSHIP, JUNIOR, LEAD, REVIEW, SENIOR, UNKNOWN,
    classify_seniority, seniority_gate,
)


def job(title, description="", employment=""):
    return Job("greenhouse", "x", "1", title, "Acme", "Remote",
               "https://example.com/job", workplace_type="remote",
               employment_type=employment, description=description)


class TestSeniority(unittest.TestCase):
    def test_internship(self):
        self.assertEqual(classify_seniority(job("Software Engineering Intern")), INTERNSHIP)
        self.assertEqual(classify_seniority(job("Data Analyst Co-op")), INTERNSHIP)

    def test_graduate_fresher_trainee_junior(self):
        for t in ["Graduate Software Engineer", "Fresher DevOps Engineer",
                  "Trainee Developer", "Junior Backend Engineer",
                  "Entry-Level Data Engineer", "New Grad SRE"]:
            self.assertEqual(classify_seniority(job(t)), JUNIOR, t)

    def test_zero_to_two_years_is_junior(self):
        self.assertEqual(classify_seniority(
            job("Software Engineer", description="0-2 years of experience required")), JUNIOR)
        self.assertEqual(classify_seniority(
            job("Backend Engineer", description="1+ years experience")), JUNIOR)

    def test_intermediate(self):
        self.assertEqual(classify_seniority(job("Software Engineer II")), INTERMEDIATE)
        self.assertEqual(classify_seniority(
            job("Engineer", description="3-5 years of experience")), INTERMEDIATE)

    def test_senior_staff(self):
        self.assertEqual(classify_seniority(job("Senior Software Engineer")), SENIOR)
        self.assertEqual(classify_seniority(job("Staff Engineer")), SENIOR)
        self.assertEqual(classify_seniority(job("Principal Engineer")), SENIOR)

    def test_lead_manager(self):
        self.assertEqual(classify_seniority(job("Engineering Manager")), LEAD)
        self.assertEqual(classify_seniority(job("Tech Lead")), LEAD)
        self.assertEqual(classify_seniority(job("Director of Engineering")), LEAD)

    def test_borderline_review(self):
        # senior in title but junior-looking experience band -> review
        self.assertEqual(classify_seniority(
            job("Senior Engineer", description="1-2 years experience")), REVIEW)
        self.assertEqual(classify_seniority(job("Junior Senior Engineer")), REVIEW)

    def test_unknown_is_default(self):
        self.assertEqual(classify_seniority(job("Engineer")), UNKNOWN)

    def test_level_coded_titles(self):
        # "Software Engineer 3" is clearly not junior.
        self.assertEqual(classify_seniority(job("Software Engineer 3")), SENIOR)
        self.assertEqual(classify_seniority(job("Software Engineer 2")), INTERMEDIATE)
        self.assertEqual(classify_seniority(job("Software Engineer I")), JUNIOR)

    def test_bigtech_level_codes(self):
        # L3 is ambiguous across firms -> review; L4+ is above junior target.
        self.assertEqual(classify_seniority(job("Software Engineer (L3)")), REVIEW)
        self.assertEqual(classify_seniority(job("Software Engineer (L4)")), SENIOR)
        self.assertEqual(classify_seniority(job("Software Engineer (L5)")), SENIOR)

    def test_seniority_gate(self):
        self.assertEqual(seniority_gate(INTERNSHIP), "actionable")
        self.assertEqual(seniority_gate(JUNIOR), "actionable")
        self.assertEqual(seniority_gate(INTERMEDIATE), "gated")
        self.assertEqual(seniority_gate(SENIOR), "gated")
        self.assertEqual(seniority_gate(LEAD), "gated")
        self.assertEqual(seniority_gate(REVIEW), "review")
        self.assertEqual(seniority_gate(UNKNOWN), "review")


if __name__ == "__main__":
    unittest.main()
