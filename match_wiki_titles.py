import csv
import gzip
import re
import unicodedata
from pathlib import Path


DATA_DIR = Path("data")
DOCUMENTS_FILE = DATA_DIR / "documents.tsv"
WIKI_TITLES_FILE = DATA_DIR / "wiki" / "enwiki-latest-all-titles-in-ns0.gz"
OUTPUT_FILE = DATA_DIR / "wiki_title_matches.tsv"


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("_", " ")
    value = value.casefold()
    return re.sub(r"\s+", " ", value).strip()


def read_game_documents() -> list[dict[str, str]]:
    with DOCUMENTS_FILE.open("r", encoding="utf-8", newline="") as file:
        return [
            row
            for row in csv.DictReader(file, delimiter="\t")
            if row["page_type"] == "game_detail"
            and row["title"].strip()
        ]


def read_wiki_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    with gzip.open(
        WIKI_TITLES_FILE,
        "rt",
        encoding="utf-8",
        errors="replace",
    ) as file:
        for line in file:
            title = line.rstrip("\r\n").replace("_", " ")
            key = normalize_title(title)
            if key and key not in titles:
                titles[key] = title
    return titles


def write_matches(
    games: list[dict[str, str]],
    wiki_titles: dict[str, str],
) -> int:
    matched = 0
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "document_id",
                "game_title",
                "gamepressure_url",
                "normalized_title",
                "wiki_title",
                "title_match",
            ],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for game in games:
            normalized_title = normalize_title(game["title"])
            wiki_title = wiki_titles.get(normalized_title, "")
            if wiki_title:
                matched += 1
            writer.writerow(
                {
                    "document_id": game["document_id"],
                    "game_title": game["title"],
                    "gamepressure_url": game["url"],
                    "normalized_title": normalized_title,
                    "wiki_title": wiki_title,
                    "title_match": "yes" if wiki_title else "no",
                }
            )
    return matched


def main() -> None:
    games = read_game_documents()
    wiki_titles = read_wiki_titles()
    matched = write_matches(games, wiki_titles)
    print(f"Gamepressure game documents: {len(games)}")
    print(f"Wikipedia titles: {len(wiki_titles)}")
    print(f"Potential title matches: {matched}")
    print(f"Without a title match: {len(games) - matched}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
