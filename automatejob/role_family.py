"""Engineering role-family classification (title-driven).

Classifies a job's TITLE into a target engineering family, a non-engineering
category, or "review" (ambiguous). The DESCRIPTION is intentionally NOT used
to turn a non-engineering title into an engineering role — a recruiter's JD
that mentions "DevOps" or "cloud" teams must still be excluded.

Families:
  * target (actionable-eligible): devops, sre, platform, infrastructure,
    cloud, backend, ai_automation, qa, software_engineering
  * other_engineering  — real engineering outside the target (security,
    frontend, mobile, ...); kept out of the shortlist, sent to review
  * non_engineering    — HR, sales, support, recruiting, ops, finance, ...
  * review             — ambiguous (could be engineering or not)
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .models import Job

DEVOPS = "devops"
SRE = "sre"
PLATFORM = "platform"
INFRASTRUCTURE = "infrastructure"
CLOUD = "cloud"
BACKEND = "backend"
AI_AUTOMATION = "ai_automation"
QA = "qa"
SOFTWARE_ENGINEERING = "software_engineering"
OTHER_ENGINEERING = "other_engineering"
NON_ENGINEERING = "non_engineering"
REVIEW = "review"

TARGET_FAMILIES = {
    DEVOPS, SRE, PLATFORM, INFRASTRUCTURE, CLOUD, BACKEND,
    AI_AUTOMATION, QA, SOFTWARE_ENGINEERING,
}

# Target families, checked in order. Longer/specific phrases are listed so
# that "Forward Deployed Software Engineer" still lands in software_engineering
# while bare "Forward Deployed Engineer" falls through to review.
_FAMILY_PATTERNS: List[Tuple[str, List[str]]] = [
    (QA, [
        "qa engineer", "quality assurance", "quality engineer", "test engineer",
        "software engineer in test", "software development engineer in test",
        "sdet", "test automation", "automation test",
    ]),
    (SRE, [
        "site reliability", "sre", "reliability engineer",
    ]),
    (DEVOPS, [
        "devops", "dev ops", "developer operations",
    ]),
    (PLATFORM, [
        "platform engineer", "platform engineering",
    ]),
    (INFRASTRUCTURE, [
        "infrastructure engineer", "infrastructure", "infra engineer",
        "production infrastructure", "systems engineer",
    ]),
    (CLOUD, [
        "cloud engineer", "cloud infrastructure", "cloud platform",
        "cloud operations", "aws", "azure", "gcp", "google cloud",
        "kubernetes", "k8s", "terraform", "serverless",
    ]),
    (AI_AUTOMATION, [
        "machine learning", "ml engineer", "ai engineer", "artificial intelligence",
        "llm", "large language", "data engineer", "nlp", "generative", "rag",
        "ml ops", "mlops", "research engineer", "automation engineer",
        "applied ai", "ai/ml",
    ]),
    (BACKEND, [
        "backend", "back-end", "back end", "golang", "python", "api engineer",
        "microservices", "distributed systems", "grpc", "full stack", "fullstack",
    ]),
    (SOFTWARE_ENGINEERING, [
        "software engineer", "software developer", "software engineering",
        "sde", "web developer", "application developer", "application engineer",
        "integration engineer", "systems engineer",
    ]),
]

# Unambiguous non-engineering core roles, checked BEFORE target families so
# that "Solutions Architect - Generative AI" and "Sales Engineer - AI" are not
# mis-classified as engineering just because an AI/cloud keyword appears.
_STRONG_NONENG_PATTERNS: List[str] = [
    "solutions architect", "solutions architecture", "solutions engineer",
    "solution engineer", "sales engineer", "presales", "pre-sales",
    "account executive", "account manager", "account development",
    "sales development", "sales specialist", "sales", "business development",
    "customer success", "customer support", "support engineer",
    "support specialist", "technical support", "technical account manager",
    "technical writer", "recruiter", "talent acquisition", "sourcer",
    "human resources", "people operations", "executive assistant",
    "hr", "tse",  # HR; TSE = Technical Support/Services Engineer (support)
    "economist", "attorney", "counsel", "consultant", "product manager",
    "product designer", "program manager", "project manager", "marketing",
    "finance", "accounting", "legal", "forward deployed engineer",
    "deployment strategist", "delivery solutions architect",
]

# Non-engineering title markers (checked after target families).
_NONENG_PATTERNS: List[str] = [
    "talent strategist", "talent coordinator", "talent", "people partner",
    "people", "hrbp", "head of people",
    "client success", "client partner", "developer advocate",
    "developer relations", "devrel", "field engineer", "technical services",
    "technical program manager", "product operations",
    "product specialist", "operations associate", "operations",
    "analyst", "coordinator", "specialist", "administrative",
    "office manager", "strategist", "renewals", "abuse investigator",
    "contract", "deal team", "business affairs", "business partner",
    "strategy", "engagement manager", "partner development",
    "partner manager", "technical account", "customer experience",
]

# Engineering-but-not-target markers -> other_engineering (review).
_OTHER_ENG_PATTERNS: List[str] = [
    "security engineer", "security", "frontend", "front-end", "front end",
    "mobile", "ios", "android", "embedded", "firmware", "hardware", "blockchain",
    "crypto", "network engineer", "data scientist", "data science", "game",
    "graphics", "devops architect", "data architect", "design engineer",
]


def _hit(pattern: str, text: str) -> bool:
    """Word-boundary match for short tokens, substring for longer phrases."""
    if len(pattern) <= 4:
        return bool(re.search(r"\b" + re.escape(pattern) + r"\b", text))
    return pattern in text


def classify_role_family(job: Job) -> str:
    """Return the role family for a job, using its title (not description)."""
    title = (job.title or "").strip().lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return REVIEW

    # 0. Unambiguous non-engineering core roles.
    for p in _STRONG_NONENG_PATTERNS:
        if _hit(p, title):
            return NON_ENGINEERING

    # 1. Target engineering families.
    for family, patterns in _FAMILY_PATTERNS:
        for p in patterns:
            if _hit(p, title):
                return family

    # 2. Clearly non-engineering.
    for p in _NONENG_PATTERNS:
        if _hit(p, title):
            return NON_ENGINEERING

    # 3. Engineering but outside the target.
    for p in _OTHER_ENG_PATTERNS:
        if _hit(p, title):
            return OTHER_ENGINEERING

    # 4. Generic engineering nouns.
    if re.search(r"\bengineer\b", title) or re.search(r"\bdeveloper\b", title):
        return SOFTWARE_ENGINEERING

    # 5. Ambiguous.
    return REVIEW
