"""Safe HTML-to-text conversion for job descriptions.

Job-board APIs return descriptions as HTML strings (Greenhouse `content`,
Ashby `descriptionHtml`). We never render or execute that HTML — we only
strip tags and decode entities so the text can be keyword-searched.

Greenhouse in particular HTML-entity-encodes the description a second time
(e.g. ``&amp;lt;p&amp;gt;``), so we decode entities repeatedly until the
string stops changing, then drop tags.
"""

from __future__ import annotations

import html as _html
import re

# Blocks whose content should not count as searchable prose.
_DROP_TAGS = re.compile(
    r"<(script|style|noscript|template|svg|math)\b[^>]*>.*?</\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
# Common block tags -> newline so "Foo</p><p>Bar" doesn't run together.
_BLOCK = re.compile(r"</?(?:p|br|div|li|ul|ol|h[1-6]|tr|table|section|article)[^>]*>",
                    flags=re.IGNORECASE)
_WS = re.compile(r"[ \t\r\f\v]+")


def decode_entities(text: str) -> str:
    """Decode HTML entities repeatedly until stable (handles double-encoding)."""
    s = text or ""
    for _ in range(4):
        decoded = _html.unescape(s)
        if decoded == s:
            break
        s = decoded
    return s


def html_to_text(raw: str | None) -> str:
    """Convert an HTML fragment/string to plain searchable text.

    Returns '' for None/empty input and for input that is not actually HTML
    (we tolerate both, since callers may pass plain text).
    """
    if not raw:
        return ""
    s = decode_entities(str(raw))
    # If after decoding there are still no tags, it was plain text all along.
    if "<" not in s:
        return _WS.sub(" ", s).strip()
    s = _DROP_TAGS.sub(" ", s)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = decode_entities(s)  # entities inside attribute-free text leftovers
    s = s.replace("\u00a0", " ")
    s = _WS.sub(" ", s)
    # Collapse runs of blank lines left by block tags.
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()
