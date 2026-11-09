"""automatejob — a personal AI-powered job hunting assistant (MVP).

Collects job postings from permitted public job-board feeds (Greenhouse and
Lever), stores them deduplicated in SQLite, and scores how well each matches
a configured target profile (roles, locations, seniority) with explainable
reasons.

This package never sends emails, applies to jobs, or connects to personal
accounts. It only reads public, documented job-board APIs.
"""

__version__ = "0.1.0"
