import re
import unicodedata
from html import unescape


PAGE_PATTERN = re.compile(r"<page\b[^>]*>(.*?)</page>", re.IGNORECASE | re.DOTALL)
TITLE_PATTERN = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TEXT_PATTERN = re.compile(
    r"<text\b[^>]*>(.*?)</text>", re.IGNORECASE | re.DOTALL
)
ID_PATTERN = re.compile(r"<id\b[^>]*>(\d+)</id>", re.IGNORECASE)
REDIRECT_PATTERN = re.compile(
    r"<redirect\b[^>]*/?>", re.IGNORECASE
)
CATEGORY_PATTERN = re.compile(
    r"\[\[\s*(Category:[^\]|#]+)(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)


def remove_templates(value: str) -> str:
    result: list[str] = []
    index = 0
    depth = 0
    while index < len(value):
        if value.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if depth and value.startswith("}}", index):
            depth -= 1
            index += 2
            continue
        if depth == 0:
            result.append(value[index])
        index += 1
    return "".join(result)


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("_", " ")
    value = value.casefold()
    return re.sub(r"\s+", " ", value).strip()


def clean_wikitext(value: str) -> str:
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    value = remove_templates(value)
    value = re.sub(r"<ref\b[^>]*>.*?</ref>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<ref\b[^>]*/>", " ", value, flags=re.I)
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"'{2,}", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def first_paragraph(wikitext: str) -> str:
    paragraphs = re.split(r"\n\s*\n", wikitext)
    for paragraph in paragraphs:
        cleaned = clean_wikitext(paragraph)
        if cleaned:
            return cleaned
    return ""


def parse_page(page_xml: str) -> dict[str, str] | None:
    title_match = TITLE_PATTERN.search(page_xml)
    text_match = TEXT_PATTERN.search(page_xml)
    if not title_match or not text_match or REDIRECT_PATTERN.search(page_xml):
        return None

    title = clean_wikitext(title_match.group(1))
    wikitext = text_match.group(1)
    page_id_match = ID_PATTERN.search(page_xml)
    categories = sorted(
        {
            clean_wikitext(category)
            for category in CATEGORY_PATTERN.findall(wikitext)
        }
    )
    return {
        "wiki_id": page_id_match.group(1) if page_id_match else "",
        "wiki_title": title,
        "normalized_title": normalize_title(title),
        "first_paragraph": first_paragraph(wikitext),
        "categories": "; ".join(categories),
    }


def extract_pages(xml_chunk: str) -> list[dict[str, str]]:
    pages = []
    for match in PAGE_PATTERN.finditer(xml_chunk):
        page = parse_page(match.group(0))
        if page is not None:
            pages.append(page)
    return pages
