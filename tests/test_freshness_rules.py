import unittest
from datetime import datetime, timedelta, timezone

from automatejob.freshness import (
    FRESHNESS_CLOSED, FRESHNESS_NEWLY_POSTED, FRESHNESS_RECENTLY_VERIFIED,
    FRESHNESS_STATUS_UNCERTAIN, FRESHNESS_VERIFICATION_OVERDUE,
    freshness_status, is_closed_evidence, within_window,
)


def _iso(dt):
    return dt.isoformat()


NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


class TestFreshness(unittest.TestCase):
    def test_newly_posted_within_48h(self):
        self.assertEqual(
            freshness_status(published_at=_iso(NOW - timedelta(hours=10)), now=NOW),
            FRESHNESS_NEWLY_POSTED)

    def test_recently_verified_for_old_listing(self):
        self.assertEqual(
            freshness_status(published_at=_iso(NOW - timedelta(days=30)),
                             last_verified_at=_iso(NOW - timedelta(hours=5)), now=NOW),
            FRESHNESS_RECENTLY_VERIFIED)

    def test_verification_overdue(self):
        self.assertEqual(
            freshness_status(published_at=_iso(NOW - timedelta(days=10)),
                             last_verified_at=_iso(NOW - timedelta(days=3)),
                             last_seen_at=_iso(NOW - timedelta(days=3)), now=NOW),
            FRESHNESS_VERIFICATION_OVERDUE)

    def test_status_uncertain_when_no_timestamps(self):
        self.assertEqual(freshness_status(now=NOW), FRESHNESS_STATUS_UNCERTAIN)

    def test_closed_wins(self):
        self.assertEqual(
            freshness_status(status="closed",
                             published_at=_iso(NOW - timedelta(hours=1)), now=NOW),
            FRESHNESS_CLOSED)

    def test_network_error_is_not_closed_evidence(self):
        self.assertFalse(is_closed_evidence("timeout", 3, close_after_scans=2))
        self.assertFalse(is_closed_evidence("error", 3))
        self.assertFalse(is_closed_evidence("success", 1))
        self.assertTrue(is_closed_evidence("success", 2))

    def test_within_window(self):
        self.assertTrue(within_window(_iso(NOW - timedelta(hours=1)), now=NOW))
        self.assertFalse(within_window(_iso(NOW - timedelta(hours=49)), now=NOW))
        self.assertFalse(within_window("", now=NOW))


if __name__ == "__main__":
    unittest.main()
