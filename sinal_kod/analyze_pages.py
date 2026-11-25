import csv
import re
from html import unescape
from pathlib import Path

from text_extractor import TextExtractor


DATA_DIR = Path("data")
PAGES_FILE = DATA_DIR / "pages.tsv"
DOCUMENTS_FILE = DATA_DIR / "documents.tsv"


class GamePageExtractor:
    """Extracts game fields from saved HTML using small regular expressions."""

    title_pattern = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
    h1_pattern = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
    description_pattern = re.compile(
        r"""<meta\b[^>]*(?:name|property)\s*=\s*["']
        (?:description|og:description)["'][^>]*\bcontent\s*=\s*["']
        (.*?)["']""",
        re.I | re.S | re.X,
    )
    release_pattern = re.compile(
        r"Release Date:\s*<b[^>]*>(.*?)</b>",
        re.I | re.S,
    )
    developer_pattern = re.compile(
        r"Developer:</span>\s*(?:<[^>]+>)*\s*(?:<b[^>]*>)?(.*?)(?:</b>)?</a>",
        re.I | re.S,
    )
    publisher_pattern = re.compile(
        r"Publisher:</span>\s*(?:<[^>]+>)*\s*(?:<b[^>]*>)?(.*?)(?:</b>)?</a>",
        re.I | re.S,
    )
    tags_pattern = re.compile(
        r'<p[^>]*class="[^"]*S016-box-tags[^"]*"[^>]*>(.*?)</p>',
        re.I | re.S,
    )
    tag_pattern = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
    platform_pattern = re.compile(
        r'<img\b[^>]*\balt="([^"]+)"[^>]*class="p-ico40"',
        re.I | re.S,
    )
    full_date_pattern = re.compile(
        r"^(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2}),\s+(\d{4})$",
        re.I,
    )
    month_numbers = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
    }

    @staticmethod
    def clean(value: str) -> str:
        return TextExtractor.extract_text(unescape(value))

    @classmethod
    def first(cls, pattern: re.Pattern[str], html: str) -> str:
        match = pattern.search(html)
        return cls.clean(match.group(1)) if match else ""

    @classmethod
    def normalize_date(cls, value: str) -> str:
        match = cls.full_date_pattern.fullmatch(value.strip())
        if not match:
            return value
        month, day, year = match.groups()
        return f"{int(day):02d}-{cls.month_numbers[month.lower()]}-{year}"

    @classmethod
    def extract(cls, html: str) -> dict[str, str | int]:
        title = cls.first(cls.title_pattern, html)
        h1 = cls.first(cls.h1_pattern, html)
        description = cls.first(cls.description_pattern, html)
        release_date = cls.first(cls.release_pattern, html)
        developer = cls.first(cls.developer_pattern, html)
        publisher = cls.first(cls.publisher_pattern, html)

        tags_match = cls.tags_pattern.search(html)
        tags = []
        if tags_match:
            tags = [
                cls.clean(match.group(1))
                for match in cls.tag_pattern.finditer(tags_match.group(1))
            ]

        platforms = []
        for platform in cls.platform_pattern.findall(html):
            platform = cls.clean(platform)
            if platform and platform not in platforms:
                platforms.append(platform)

        is_game_detail = bool(
            h1
            and release_date
            and (developer or publisher)
            and "S016-box" in html
        )
        page_type = "game_detail" if is_game_detail else "other"
        text = TextExtractor.extract_text(html)

        return {
            "title": h1 or title,
            "description": description,
            "release_date_raw": release_date,
            "release_date": cls.normalize_date(release_date),
            "developer": developer,
            "publisher": publisher,
            "genres": "; ".join(tags),
            "platforms": "; ".join(platforms),
            "page_type": page_type,
            "text_characters": len(text),
            "text": text,
        }


def read_pages() -> list[dict[str, str]]:
    with PAGES_FILE.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def main() -> None:
    analyzed = 0
    page_types: dict[str, int] = {}
    with DOCUMENTS_FILE.open("w", encoding="utf-8", newline="") as output:
        fieldnames = [
            "document_id",
            "url",
            "html_file",
            "page_type",
            "title",
            "description",
            "release_date_raw",
            "release_date",
            "developer",
            "publisher",
            "genres",
            "platforms",
            "text_characters",
            "text",
        ]
        writer = csv.DictWriter(
            output, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()

        for analyzed, page in enumerate(read_pages(), start=1):
            html_path = Path(page["html_file"])
            if not html_path.is_absolute():
                html_path = Path.cwd() / html_path
            html = html_path.read_text(encoding="utf-8")
            extracted = GamePageExtractor.extract(html)
            extracted.update(
                {
                    "document_id": f"doc_{analyzed:06d}",
                    "url": page["url"],
                    "html_file": page["html_file"],
                }
            )
            writer.writerow(extracted)
            page_type = str(extracted["page_type"])
            page_types[page_type] = page_types.get(page_type, 0) + 1

    print(f"Analyzed documents: {analyzed}")
    for page_type, count in sorted(page_types.items()):
        print(f"{page_type}: {count}")
    print(f"Output: {DOCUMENTS_FILE}")


if __name__ == "__main__":
    main()
