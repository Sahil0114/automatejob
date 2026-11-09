"""Adversarial regression tests for role-family classification.

These lock in the fixes for the previously mis-ranked titles: recruiters, HR,
support, sales, operations, solutions architects and other non-engineering
roles must be classified non_engineering (or review), never a target
engineering family — regardless of any engineering keyword in the title.
"""

import unittest

from automatejob.models import Job
from automatejob.role_family import (
    NON_ENGINEERING,
    OTHER_ENGINEERING,
    REVIEW,
    TARGET_FAMILIES,
    classify_role_family,
)


def fam(title: str) -> str:
    return classify_role_family(Job("x", "x", "0", title, "Acme", "", ""))


class TestNonEngineeringExclusion(unittest.TestCase):
    def test_recruiting_and_hr(self):
        for t in ["Associate Recruiter", "Associate, HR Shared Services",
                  "Tech Sourcer (Contract)", "Talent Coordinator",
                  "Talent Strategist", "People Operations Partner",
                  "Employer Brand Analyst", "Candidate Experience Coordinator, Contract"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_support_and_services(self):
        for t in ["Associate TSE", "Associate TSE II",
                  "Enterprise Support Specialist (Bengaluru, India)",
                  "Personalized Support Engineer", "Technical Services Engineer",
                  "Technical Support Engineer", "Support Engineer",
                  "Community Support Specialist (Bengaluru, India)",
                  "Customer Support Specialist (Bengaluru, India)",
                  "Technical Quality Specialist (Bengaluru, India)",
                  "Product Support Specialist"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_sales_and_account_management(self):
        for t in ["Account Development Representative", "Account Executive",
                  "Mid Market Account Executive", "Strategic Account Executive",
                  "Customer Success Associate (Commercial Accounts)",
                  "Customer Success Manager", "Sales Engineer",
                  "Business Development Representative",
                  "Renewals Specialist"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_operations_finance_legal(self):
        for t in ["Credit Risk Operations Associate", "Fraud Operations Associate",
                  "Operations Associate, Apprenticeship",
                  "Product Support Operations Associate",
                  "Verifications Operations Associate", "Contract Operations",
                  "Finance Data and AI Lead", "Regional Research Economist",
                  "Strategic Finance Analyst", "Regulatory Attorney",
                  "Corporate Counsel", "Deal Team - Business Affairs",
                  "Executive Assistant", "Technical Writer"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_solutions_architect_and_presales(self):
        # "Solutions Architect - Generative AI" must NOT leak into ai_automation.
        for t in ["Solutions Architect", "Senior Solutions Architect",
                  "Specialist Solutions Architect - AI/ML",
                  "Lead Solutions Architect - Generative AI (EMEA)",
                  "Delivery Solutions Architect", "Solutions Engineer",
                  "Sr. Solutions Engineer", "Forward Deployed Engineer",
                  "Deployment Strategist", "Sales Engineer"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_product_and_program(self):
        for t in ["Product Manager", "Staff Product Manager",
                  "Technical Program Manager", "Program Manager",
                  "Project Manager", "Product Designer",
                  "Product Operations Manager"]:
            self.assertEqual(fam(t), NON_ENGINEERING, t)

    def test_customer_facing_social(self):
        self.assertEqual(fam("Social Media, Customer Support Associate"), NON_ENGINEERING)


class TestEngineeringInclusion(unittest.TestCase):
    def test_target_engineering_families(self):
        cases = {
            "DevOps Engineer": "devops",
            "Site Reliability Engineer": "sre",
            "Platform Engineer": "platform",
            "Infrastructure Engineer": "infrastructure",
            "Cloud Operations Engineer": "cloud",
            "Backend Engineer": "backend",
            "Data Engineer": "ai_automation",
            "Machine Learning Engineer": "ai_automation",
            "QA Engineer": "qa",
            "Test Automation Engineer": "qa",
            "Software Engineer": "software_engineering",
            "Software Engineer, New Grad": "software_engineering",
            "Software Engineer, Intern": "software_engineering",
            "Senior Software Engineer": "software_engineering",
            "Software Engineer 3": "software_engineering",
            "Sr. Software Engineer- Databricks AI": "software_engineering",
            "Python Developer": "backend",
        }
        for title, family in cases.items():
            self.assertEqual(fam(title), family, title)

    def test_multi_family_titles_are_target(self):
        for t in ["Cloud Platform Engineer", "AI Platform Engineer",
                  "Software Engineer - Spark", "Principal, Machine Learning Engineer (L5)"]:
            self.assertIn(fam(t), TARGET_FAMILIES, t)

    def test_forward_deployed_software_engineer_is_engineering(self):
        self.assertEqual(fam("Forward Deployed Software Engineer"), "software_engineering")
        self.assertEqual(fam("Forward Deployed Infrastructure Engineer"), "infrastructure")

    def test_security_engineer_is_other_engineering(self):
        self.assertEqual(fam("Staff Security Engineer, Abuse Control"), OTHER_ENGINEERING)
        self.assertEqual(fam("Senior Identity Security Engineer"), OTHER_ENGINEERING)


if __name__ == "__main__":
    unittest.main()
