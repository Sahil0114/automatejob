import unittest

from automatejob.source_registry import SourceRegistry, SourceStatus


class TestSourceRegistry(unittest.TestCase):
    def setUp(self):
        self.r = SourceRegistry(":memory:")

    def tearDown(self):
        self.r.close()

    def test_registers_and_lists_sources_without_marking_unsupported_active(self):
        self.r.upsert("yc_waas", "yc_waas", access="api", classification="api",
                     auth_required=False, permission="authorized",
                     status=SourceStatus.ACTIVE, refresh_policy="incremental")
        self.r.upsert("apollo", "apollo", access="api", classification="api",
                     auth_required=True, permission="unauthorized",
                     status=SourceStatus.UNSUPPORTED)
        rows = self.r.list_active()
        self.assertEqual([r["name"] for r in rows], ["yc_waas"])
        all_rows = self.r.list_all()
        self.assertEqual(len(all_rows), 2)

    def test_records_success_and_errors(self):
        self.r.upsert("hn", "hn", status=SourceStatus.ACTIVE)
        sid = self.r.get("hn")["id"]
        self.r.record_success(sid, verified_records=42)
        self.r.record_error(sid, "timeout")
        row = self.r.get("hn")
        self.assertEqual(row["last_successful_at"] != "", True)
        self.assertEqual(row["last_verified_records"], 42)
        self.assertEqual(row["error_count"], 1)


if __name__ == "__main__":
    unittest.main()
