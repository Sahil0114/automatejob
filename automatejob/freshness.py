"""48-hour freshness rules for jobs, recruiter posts and hiring signals.

This module distinguishes publication time from collection/verification time.
The six timestamp columns (published_at / first_seen_at / last_seen_at /
last_verified_at / source_updated_at / collected_at) are independent and never
conflated.

Freshness statuses:
  * newly_posted        — original publication within the last 48 hours.
  * recently_verified   — verified against its source within the last 48 hours
                          (regardless of the listing's age).
  * verification_overdue — last verification older than 48 hours.
  * status_uncertain    — source unavailable/ambiguous; no confident answer.
  * closed              — sufficient evidence of closure (never inferred from a
                          network error, 403 or timeout).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

FRESHNESS_NEWLY_POSTED = "newly_posted"
FRESHNESS_RECENTLY_VERIFIED = "recently_verified"
FRESHNESS_VERIFICATION_OVERDUE = "verification_overdue"
FRESHNESS_STATUS_UNCERTAIN = "status_uncertain"
FRESHNESS_CLOSED = "closed"

WINDOW = timedelta(hours=48)


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # RFC-822 / ISO with offset handled by fromisoformat best-effort.
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def age_since(value: str, now: datetime | None = None) -> timedelta | None:
    """Return the age of a timestamp (or None when absent/unparseable)."""
    dt = _parse(value)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return now - dt


def within_window(value: str, window: timedelta = WINDOW,
                  now: datetime | None = None) -> bool:
    """True when `value` is within `window` of `now`."""
    age = age_since(value, now=now)
    return age is not None and age <= window


def freshness_status(*, status: str = "open", published_at: str = "",
                     last_verified_at: str = "", last_seen_at: str = "",
                     now: datetime | None = None) -> str:
    """Compute the 48-hour freshness status for a listing.

    A ``closed`` status always wins. A network error never produces ``closed``
    here — callers must only pass ``closed`` when there is sufficient evidence.
    """
    if status == "closed":
        return FRESHNESS_CLOSED

    # A fresh publication is the strongest "new" signal.
    if within_window(published_at, now=now):
        return FRESHNESS_NEWLY_POSTED

    # A recent successful verification is good even for older listings.
    if within_window(last_verified_at, now=now):
        return FRESHNESS_RECENTLY_VERIFIED

    # If we can't even tell when it was last seen or verified, it is uncertain.
    if not last_verified_at and not last_seen_at:
        return FRESHNESS_STATUS_UNCERTAIN

    return FRESHNESS_VERIFICATION_OVERDUE


def age_seconds_since(value: str, now: datetime | None = None) -> int | None:
    age = age_since(value, now=now)
    if age is None:
        return None
    return int(age.total_seconds())


def is_closed_evidence(source_status: str, absent_scans: int,
                       close_after_scans: int = 2) -> bool:
    """Closed is only claimed from successful-source absence evidence, never a
    transient network error, 403 or timeout."""
    if source_status != "success":
        return False
    return absent_scans >= close_after_scans
