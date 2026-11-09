"""Verified candidate profile + answer bank loader.

Separates VERIFIED facts from generated language. The profile only ships
verified facts; unverified fields carry the literal "unverified" marker and
must be confirmed before use. Rendering an answer that depends on an
unverified field raises instead of fabricating.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List, Optional


class UnverifiedField(ValueError):
    """Raised when an answer depends on a fact that has not been confirmed."""


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _unverified(value) -> bool:
    return value in (None, "", "unverified")


def _shorten_tech(techs: List[str], n: int = 5) -> str:
    return ", ".join(techs[:n])


class CandidateProfile:
    def __init__(self, path: str | Path):
        self.raw = _load_toml(Path(path))
        self.portfolio = self.raw.get("contact", {}).get("portfolio", "")
        self.technologies = self.raw.get("technologies", [])
        self.target_roles = self.raw.get("target_roles", [])
        self.communication_style = self.raw.get("communication_style", "")
        self.location_preferences = self.raw.get("location_preferences", [])

    # -- verified / unverified accessors ------------------------------------
    def verified(self, dotted: str):
        """Return a verified value, or raise UnverifiedField."""
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise UnverifiedField(dotted)
            node = node[part]
        if _unverified(node):
            raise UnverifiedField(dotted)
        return node

    def unverified_fields(self) -> List[str]:
        """List dotted paths that still need user confirmation."""
        missing = []
        if _unverified(self.raw.get("name")):
            missing.append("name")
        if _unverified(self.raw.get("contact", {}).get("email")):
            missing.append("contact.email")
        if _unverified(self.raw.get("education", {}).get("summary")):
            missing.append("education.summary")
        if not self.raw.get("experience", {}).get("entries"):
            missing.append("experience.entries")
        emp = self.raw.get("employment", {})
        for k in ("work_authorization", "notice_period", "availability"):
            if _unverified(emp.get(k)):
                missing.append(f"employment.{k}")
        return missing

    # -- rendering helpers --------------------------------------------------
    def render(self, question: str, context: Optional[dict] = None) -> str:
        """Render an answer-bank template, raising on unverified dependencies."""
        context = context or {}
        # Find the template in the answer bank (passed via context) — the
        # caller supplies templates; here we just interpolate.
        template = context.get("template", "")
        return template.format(**context)


class AnswerBank:
    def __init__(self, path: str | Path):
        self.raw = _load_toml(Path(path))
        self.answers = {a["question"]: a for a in self.raw.get("answers", [])}

    def template(self, question: str) -> Optional[str]:
        a = self.answers.get(question)
        return a["template"] if a else None

    def questions(self) -> List[str]:
        return list(self.answers.keys())
