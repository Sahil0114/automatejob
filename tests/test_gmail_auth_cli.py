import unittest

from automatejob.cli import build_parser


class TestGmailAuthCLI(unittest.TestCase):
    def test_gmail_auth_command_accepts_local_paths(self):
        args = build_parser().parse_args([
            "gmail-auth", "--credentials-file", "/tmp/client.json", "--token-file", "/tmp/token.json",
        ])
        self.assertEqual(args.command, "gmail-auth")
        self.assertEqual(args.credentials_file, "/tmp/client.json")
        self.assertEqual(args.token_file, "/tmp/token.json")
        self.assertTrue(callable(args.func))
    def test_gmail_sync_command_is_available(self):
        args = build_parser().parse_args(["gmail-sync"])
        self.assertEqual(args.command, "gmail-sync")
        self.assertTrue(callable(args.func))


if __name__ == "__main__":
    unittest.main()
