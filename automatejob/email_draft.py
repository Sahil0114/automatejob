"""Personalized cold-email drafting (deterministic — no LLM).

Drafts are assembled from the VERIFIED candidate profile and company evidence.
Every candidate claim maps back to the profile; every company claim maps back to
a source URL. The generator refuses to include a company-specific insight
unless one is supplied (never invents one). Style: short, natural, concise
bullets, relevant experience first, one real value-add, portfolio link.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .profile import CandidateProfile, UnverifiedField

DRAFT_VACANCY = "vacancy"
DRAFT_INTERNSHIP = "internship"
DRAFT_REFERRAL = "referral"
DRAFT_FOUNDER = "founder"
DRAFT_FOLLOWUP = "followup"
DRAFT_CTO = "cto"
DRAFT_EM = "engineering_manager"
DRAFT_RECRUITER = "recruiter"

DRAFT_TYPES = (DRAFT_VACANCY, DRAFT_INTERNSHIP, DRAFT_REFERRAL, DRAFT_FOUNDER,
               DRAFT_FOLLOWUP, DRAFT_CTO, DRAFT_EM, DRAFT_RECRUITER)

# Map a contact role_kind to the most appropriate draft template.
ROLE_TO_DRAFT = {
    "cto": DRAFT_CTO,
    "founder": DRAFT_FOUNDER,
    "ceo": DRAFT_FOUNDER,
    "engineering_manager": DRAFT_EM,
    "em": DRAFT_EM,
    "infra_lead": DRAFT_EM,
    "platform_lead": DRAFT_EM,
    "devops_lead": DRAFT_EM,
    "recruiter": DRAFT_RECRUITER,
    "talent": DRAFT_RECRUITER,
    "engineer": DRAFT_REFERRAL,
}


def draft_type_for_role(role_kind: str) -> str:
    return ROLE_TO_DRAFT.get((role_kind or "").lower(), DRAFT_REFERRAL)


def _headline_experience(profile: CandidateProfile) -> str:
    """One-line experience summary from verified history (relevant-first)."""
    if profile.experience:
        current = profile.experience[0]
        return (f"{current['title']} at {current['company']} — Go APIs, Kubernetes, "
                "Terraform and AWS automation")
    return ""


def _experience_bullets(profile: CandidateProfile) -> List[str]:
    """Concise, verified bullets pulled from the candidate's own resume."""
    bullets = []
    for exp in profile.experience[:3]:
        for b in exp.get("bullets", [])[:2]:
            # keep bullets short and concrete
            if len(b) > 130:
                b = b[:127].rstrip() + "..."
            bullets.append(b)
    return bullets[:4]


def _role_experience_map(profile: CandidateProfile) -> Dict[str, str]:
    """Map each draft claim to the profile field it comes from."""
    return {
        "name": "profile.name",
        "current_role": "profile.experience[0]",
        "experience_bullets": "profile.experience[*].bullets",
        "portfolio": "profile.portfolio",
        "github": "profile.github",
    }


def build_evidence_map(profile: CandidateProfile, company: dict,
                       evidence: List[dict], draft_type: str) -> Dict[str, str]:
    """Map every claim in a draft to its source (candidate or company)."""
    em: Dict[str, str] = {}
    em["candidate.name"] = "profile.name"
    em["candidate.experience"] = "profile.experience (resume)"
    em["candidate.portfolio"] = profile.portfolio or "profile.portfolio"
    em["company.name"] = "company registry"
    em["company.domain"] = f"company domain: {company.get('domain', '')}"
    for ev in evidence[:3]:
        key = f"company.{ev['kind']}"
        em[key] = f"{ev.get('url') or 'company evidence'} (observed {ev.get('observed_at', '')})"
    em["draft_type"] = draft_type
    return em


def draft_email(profile: CandidateProfile, company: dict, contact: Optional[dict],
                draft_type: str, evidence: Optional[List[dict]] = None,
                job_title: Optional[str] = None) -> dict:
    """Return {subject, body, evidence_map}. Raises if a required fact is unverified."""
    if draft_type not in DRAFT_TYPES:
        raise ValueError(f"unknown draft_type {draft_type!r}")

    # Refuse to draft without verified identity (name required for signature).
    name = profile.verified("name")
    portfolio = profile.portfolio
    company_name = company.get("name") or "your team"
    first_name = (contact or {}).get("first_name") or ""

    evidence = evidence or []
    insight = next((ev for ev in evidence if ev.get("kind") in (
        "product_launch", "eng_blog", "oss", "hiring", "expansion")), None)
    contact_greeting = f"Hi {first_name}," if first_name else "Hi,"

    if draft_type == DRAFT_VACANCY:
        subject = f"Application for {job_title or 'the engineering role'}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, currently a {_headline_experience(profile)}. I'm applying for "
            f"{job_title or 'the junior engineering role'} at {company_name}.",
            "",
            "A few things from my experience:",
        ]
        body_lines += [f"- {b}" for b in _experience_bullets(profile)]
        if insight:
            body_lines += ["", f"I came across your {insight['kind'].replace('_', ' ')} — "
                               f"{insight['fact']}."]
        body_lines += [
            "",
            f"I'd like to help with your infrastructure and backend automation. "
            f"Portfolio: {portfolio}",
            "",
            "Would you be open to a quick conversation?",
            "",
            name,
        ]
    elif draft_type == DRAFT_INTERNSHIP:
        subject = f"Internship / junior role at {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, a DevOps/backend engineer ({_headline_experience(profile)}). "
            f"I didn't see a posted junior or internship role, but I'd love to help "
            f"{company_name} if a spot opens up.",
            "",
            "Where I could contribute:",
        ]
        body_lines += [f"- {b}" for b in _experience_bullets(profile)[:3]]
        if insight:
            body_lines += ["", f"I noticed {insight['fact']} "
                               f"({insight.get('url', '')})."]
        body_lines += [
            "",
            f"Portfolio: {portfolio}",
            "",
            "Would an internship or junior role be possible?",
            "",
            name,
        ]
    elif draft_type == DRAFT_REFERRAL:
        subject = f"Quick question about engineering at {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, a DevOps engineer working with Go, Kubernetes and Terraform. "
            f"I'm interested in {company_name} and wanted to ask for a short referral or "
            f"a pointer to the right person on your engineering team.",
            "",
            f"Portfolio: {portfolio}",
            "",
            "Thanks for your time!",
            "",
            name,
        ]
    elif draft_type == DRAFT_FOUNDER:
        subject = f"Idea for {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, a DevOps/backend engineer. I've been building Kubernetes and "
            f"Terraform automation and Go APIs, and I think I could contribute to "
            f"{company_name}.",
            "",
            "Specifically:",
        ]
        body_lines += [f"- {b}" for b in _experience_bullets(profile)[:3]]
        if insight:
            body_lines += ["", f"I saw {insight['fact']} and wondered if you're looking "
                               f"for help on the infrastructure side."]
        body_lines += [
            "",
            f"Portfolio: {portfolio}",
            "",
            "Open to a brief chat?",
            "",
            name,
        ]
    elif draft_type in (DRAFT_CTO, DRAFT_EM):
        audience = "the team" if draft_type == DRAFT_EM else "you"
        subject = f"Backend/DevOps help for {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, a DevOps/backend engineer — I spend most of my time on "
            f"Kubernetes, Terraform and Go APIs, and I'm reaching out because "
            f"{company_name} seems like a team I could genuinely help.",
            "",
            "Recent work:",
        ]
        body_lines += [f"- {b}" for b in _experience_bullets(profile)[:3]]
        if insight:
            body_lines += ["", f"I saw {insight['fact']} ({insight.get('url', '')})."]
        body_lines += [
            "",
            f"I'd love to contribute on the infrastructure/backend side — is there "
            f"a junior role, internship, or a project I could help with?",
            "",
            f"Portfolio: {portfolio}",
            "",
            name,
        ]
    elif draft_type == DRAFT_RECRUITER:
        subject = f"Junior DevOps/backend roles at {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"I'm {name}, a DevOps/backend engineer (Go, Kubernetes, Terraform, AWS). "
            f"I'm looking for junior or new-graduate engineering roles and wanted to "
            f"check whether {company_name} has anything open, or might soon.",
            "",
            "A snapshot of my experience:",
        ]
        body_lines += [f"- {b}" for b in _experience_bullets(profile)[:3]]
        body_lines += [
            "",
            f"Portfolio: {portfolio}",
            "",
            "Would you be able to point me to the right role or team?",
            "",
            name,
        ]
    else:  # followup
        subject = f"Following up — {company_name}"
        body_lines = [
            contact_greeting,
            "",
            f"Just following up on my earlier note — I'm still very interested in "
            f"{company_name} and would be glad to share how my Kubernetes/Terraform "
            f"experience could help.",
            "",
            f"Portfolio: {portfolio}",
            "",
            "Thanks!",
            "",
            name,
        ]

    evidence_map = build_evidence_map(profile, company, evidence, draft_type)
    return {"subject": subject, "body": "\n".join(body_lines), "evidence_map": evidence_map}


def draft_for_contact(profile: CandidateProfile, company: dict, contact: dict,
                      evidence: Optional[List[dict]] = None,
                      job_title: Optional[str] = None) -> dict:
    """Generate a draft tailored to a contact's role (founder/CTO/EM/recruiter/etc)."""
    role = (contact or {}).get("role_kind", "") or ""
    draft_type = draft_type_for_role(role)
    return draft_email(profile, company, contact, draft_type,
                       evidence=evidence, job_title=job_title)
