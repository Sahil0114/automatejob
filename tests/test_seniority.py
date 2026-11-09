import unittest

from automatejob.models import Job
from automatejob.seniority import (
    INTERMEDIATE, INTERNSHIP, JUNIOR, LEAD, REVIEW, SENIOR, UNKNOWN,
    classify_seniority,
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
        # conflicting markers
        self.assertEqual(classify_seniority(job("Junior Senior Engineer")), REVIEW)

    def test_unknown_is_default(self):
        self.assertEqual(classify_seniority(job("Engineer")), UNKNOWN)


if __name__ == "__main__":
    unittest.main()
