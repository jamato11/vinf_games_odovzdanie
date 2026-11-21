import re
from urllib.parse import urldefrag, urljoin, urlparse


class LinkExtractor:
    """Only finds and normalizes href links. It does not process page text."""

    href_pattern = re.compile(
        r"""<a\b[^>]*\bhref\s*=\s*["']([^"'#]+)""",
        re.IGNORECASE,
    )
    @classmethod
    def extract_links(cls, html: str, base_url: str) -> list[str]:
        links = []
        seen = set()
        for href in cls.href_pattern.findall(html):
            url = cls.normalize_url(href, base_url)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if url not in seen:
                seen.add(url)
                links.append(url)
        return links

    @staticmethod
    def normalize_url(href: str, base_url: str) -> str:
        url = urljoin(base_url, href)
        url, _ = urldefrag(url)
        return url
