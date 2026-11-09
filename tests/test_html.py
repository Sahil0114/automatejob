import unittest

from automatejob.html import html_to_text


class TestHtmlToText(unittest.TestCase):
    def test_strips_tags(self):
        self.assertEqual(html_to_text("<p>Hello <b>world</b></p>"), "Hello world")

    def test_decodes_double_encoded_entities(self):
        # Greenhouse HTML-entity-encodes the description a second time.
        self.assertEqual(
            html_to_text("This is &amp;lt;b&amp;gt;escaped&amp;lt;/b&amp;gt; text"),
            "This is escaped text",
        )

    def test_plain_text_passthrough(self):
        self.assertEqual(html_to_text("just plain text"), "just plain text")

    def test_empty_and_none(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text(None), "")

    def test_malformed_html_does_not_crash(self):
        # Unbalanced tags / stray angle brackets must not raise.
        self.assertIsInstance(html_to_text("<div><p>broken < span < /p>"), str)
        self.assertIsInstance(html_to_text("<script>alert(1)</script>"), str)

    def test_script_and_style_removed(self):
        self.assertEqual(
            html_to_text("<script>var x=1;</script><style>p{color:red}</style>Clean"),
            "Clean",
        )

    def test_entities_decoded(self):
        self.assertEqual(html_to_text("Tom &amp; Jerry &mdash; show"), "Tom & Jerry — show")


if __name__ == "__main__":
    unittest.main()
