import unittest

from automatejob.outreach import OutreachStorage
from automatejob.workflow import sync_gmail_replies


class _Gmail:
    def __init__(self, replies):
        self.replies = replies

    def available(self):
        return True

    def thread_has_reply(self, thread_id):
        return self.replies.get(thread_id)


class TestGmailReplySync(unittest.TestCase):
    def setUp(self):
        self.s = OutreachStorage(":memory:")
        self.s.upsert_company("c1", "Acme", "acme.com", "high_signal", 50, [])
        self.s.upsert_thread("thread-1", "c1", None)
        self.s.create_followup("thread-1", "c1", None, 1, "2026-01-01", "no reply")

    def tearDown(self):
        self.s.close()

    def test_reply_sync_records_reply_and_cancels_pending_followups(self):
        result = sync_gmail_replies(self.s, _Gmail({"thread-1": True}))
        self.assertEqual(result["replies_recorded"], 1)
        self.assertEqual(self.s.list_due_followups(today="2026-12-01"), [])
        thread = self.s.conn.execute("SELECT status FROM gmail_threads WHERE thread_id='thread-1'").fetchone()
        self.assertEqual(thread["status"], "replied")

    def test_unknown_mailbox_state_does_not_assume_no_reply(self):
        result = sync_gmail_replies(self.s, _Gmail({"thread-1": None}))
        self.assertEqual(result["replies_recorded"], 0)
        self.assertEqual(result["unknown_threads"], 1)


if __name__ == "__main__":
    unittest.main()
