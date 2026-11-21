import re
from html import unescape


class TextExtractor:
    """Only turns HTML into plain text. It does not find or filter URLs."""

    unwanted_pattern = re.compile(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    tag_pattern = re.compile(r"<[^>]+>")
    whitespace_pattern = re.compile(r"\s+")

    @classmethod
    def extract_text(cls, html: str) -> str:
        html_without_code = cls.unwanted_pattern.sub(" ", html)
        text = cls.tag_pattern.sub(" ", html_without_code)
        text = unescape(text)
        return cls.whitespace_pattern.sub(" ", text).strip()
