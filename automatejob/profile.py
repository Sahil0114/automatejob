"""Verified candidate profile + answer bank loader.

Separates VERIFIED facts from generated language. Facts live in two files:

* ``data/candidate_profile.toml`` (tracked) — non-sensitive schema: target
  roles, technologies, location/employment preferences, communication style.
* ``data/personal.toml`` (git-ignored) — sensitive verified facts: name,
  contact, education, employment history, projects.

Rendering an answer that depends on an unverified field raises instead of
fabricating. Unknowns are reported as a confirmation checklist.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List, Optional


class UnverifiedField(ValueError):
    """Raised when an answer depends on a fact that has not been confirmed."""


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _unverified(value) -> bool:
    return value in (None, "", "unverified")


class CandidateProfile:
    def __init__(self, path: str | Path, personal_path: str | Path = "data/personal.toml"):
        self.path = Path(path)
        self.personal_path = Path(personal_path)
        self.raw = _load_toml(self.path)
        self.personal = _load_toml(self.personal_path)
        self.technologies = self.raw.get("technologies", [])
        self.target_roles = self.raw.get("target_roles", [])
        self.communication_style = self.raw.get("communication_style", "")
        self.location_preferences = self.raw.get("location_preferences", [])

    # -- personal facts (from git-ignored file) ------------------------------
    @property
    def name(self) -> str:
        return self.personal.get("name", "unverified")

    @property
    def email(self) -> str:
        return self.personal.get("contact", {}).get("email", "unverified")

    @property
    def portfolio(self) -> str:
        return (self.personal.get("contact", {}) or {}).get("portfolio") \
            or self.raw.get("contact", {}).get("portfolio", "")

    @property
    def github(self) -> str:
        return self.personal.get("contact", {}).get("github", "")

    @property
    def linkedin(self) -> str:
        return self.personal.get("contact", {}).get("linkedin", "")

    @property
    def education_summary(self) -> str:
        return self.personal.get("education", {}).get("summary", "unverified")

    @property
    def experience(self) -> list:
        return self.personal.get("experience", [])

    @property
    def projects(self) -> list:
        return self.personal.get("projects", []) or self.raw.get("projects", [])

    @property
    def employment_status(self) -> str:
        return self.personal.get("employment_status", "unverified")

    @property
    def notice_period(self) -> str:
        return self.personal.get("notice_period", "unverified")

    @property
    def availability(self) -> str:
        return self.personal.get("availability", "unverified")

    def verified(self, dotted: str):
        """Return a verified value, raising UnverifiedField if absent."""
        if dotted == "name" and not _unverified(self.name):
            return self.name
        if dotted == "contact.email" and not _unverified(self.email):
            return self.email
        if dotted == "education.summary" and not _unverified(self.education_summary):
            return self.education_summary
        if dotted == "experience.entries" and self.experience:
            return self.experience
        raise UnverifiedField(dotted)

    def unverified_fields(self) -> List[str]:
        """Confirmation checklist of fields still requiring user confirmation."""
        missing = []
        if _unverified(self.name):
            missing.append("name")
        if _unverified(self.email):
            missing.append("contact.email")
        if _unverified(self.education_summary):
            missing.append("education.summary")
        if not self.experience:
            missing.append("experience.entries")
        for k in ("notice_period", "availability", "work_authorization"):
            if _unverified(self.personal.get(k)):
                missing.append(k)
        return missing


class AnswerBank:
    def __init__(self, path: str | Path):
        self.raw = _load_toml(Path(path))
        self.answers = {a["question"]: a for a in self.raw.get("answers", [])}

    def template(self, question: str) -> Optional[str]:
        a = self.answers.get(question)
        return a["template"] if a else None

    def questions(self) -> List[str]:
        return list(self.answers.keys())
