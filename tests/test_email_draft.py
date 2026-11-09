import os
import tempfile
import unittest

from automatejob.email_draft import (
    DRAFT_FOUNDER, DRAFT_VACANCY, draft_email, build_evidence_map,
)
from automatejob.profile import CandidateProfile, UnverifiedField


def make_profile(personal_path=None):
    # Use a personal.toml if present, else an empty one (unverified).
    p = personal_path or os.path.join(tempfile.gettempdir(), "nonexistent_personal.toml")
    return CandidateProfile("data/candidate_profile.toml", personal_path=p)


class TestEmailDraft(unittest.TestCase):
    def test_draft_raises_without_verified_name(self):
        prof = make_profile()
        with self.assertRaises(UnverifiedField):
            draft_email(prof, {"name": "Acme", "domain": "acme.com"}, None, DRAFT_VACANCY)

    def test_draft_uses_verified_facts(self):
        prof = make_profile("data/personal.toml")
        d = draft_email(prof, {"name": "Acme", "domain": "acme.com"},
                        {"first_name": "Jane"}, DRAFT_VACANCY, job_title="DevOps Engineer")
        self.assertIn("Sahil Gada", d["body"])
        self.assertIn("opsbysahil.site", d["body"])
        self.assertEqual(d["subject"], "Application for DevOps Engineer")
        # no invented company-specific insight when none supplied
        self.assertNotIn("I noticed", d["body"])
        self.assertNotIn("I came across", d["body"])

    def test_company_insight_only_when_evidence_supplied(self):
        prof = make_profile("data/personal.toml")
        evidence = [{"kind": "product_launch", "fact": "launched X", "url": "https://x.com", "observed_at": "2026-11-01"}]
        d = draft_email(prof, {"name": "Acme", "domain": "acme.com"},
                        {"first_name": "Jane"}, DRAFT_FOUNDER, evidence=evidence)
        self.assertIn("launched X", d["body"])

    def test_evidence_map_links_claims_to_sources(self):
        prof = make_profile("data/personal.toml")
        em = build_evidence_map(prof, {"name": "Acme", "domain": "acme.com"},
                                [{"kind": "oss", "fact": "f", "url": "https://github.com/x", "observed_at": ""}],
                                DRAFT_VACANCY)
        self.assertIn("candidate.name", em)
        self.assertIn("company.oss", em)


if __name__ == "__main__":
    unittest.main()
