import argparse
import bz2
import csv
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from wiki_parser import extract_pages


DEFAULT_BASE_URL = "https://dumps.wikimedia.org/enwiki/latest/"
USER_AGENT = "FIIT-STU-GameSearch/1.0 (student project)"
GAME_CONTEXT_PATTERN = re.compile(
    r"\b(?:video game|computer game|mobile game|role-playing game|"
    r"action-adventure game|strategy game|simulation game|game developed|"
    r"game published|game by)\b",
    re.IGNORECASE,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch only matching Wikipedia multistream blocks."
    )
    parser.add_argument("--matches", required=True, help="wiki_title_matches.tsv")
    parser.add_argument("--selected-pages", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--dump-name",
        default="enwiki-latest-pages-articles-multistream.xml.bz2",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N selected pages; 0 means all",
    )
    parser.add_argument(
        "--block-size-mb",
        type=int,
        default=8,
        help="Initial byte range size for one bzip2 block",
    )
    return parser.parse_args()


def read_game_matches(path: Path) -> dict[str, list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        matches: dict[str, list[dict[str, str]]] = {}
        for row in csv.DictReader(file, delimiter="\t"):
            if row["title_match"] == "yes":
                matches.setdefault(row["normalized_title"], []).append(row)
        return matches


def read_selected_pages(path: Path, limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file, delimiter="\t"))
    rows.sort(key=lambda row: (row["xml_part"], int(row["xml_offset"])))
    unique_rows = []
    seen_offsets: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["xml_part"], row["xml_offset"])
        if key not in seen_offsets:
            seen_offsets.add(key)
            unique_rows.append(row)
    return unique_rows[:limit] if limit > 0 else unique_rows


def download_range(url: str, offset: int, initial_size: int) -> bytes:
    size = initial_size
    for attempt in range(5):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Range": f"bytes={offset}-{offset + size - 1}",
                },
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                compressed = response.read()

            decompressor = bz2.BZ2Decompressor()
            decompressed = decompressor.decompress(compressed)
            if decompressor.eof:
                return decompressed
        except (OSError, urllib.error.URLError) as error:
            if attempt == 4:
                raise RuntimeError(
                    f"Could not download block at offset {offset}"
                ) from error
            time.sleep(2**attempt)
        size *= 2
    raise RuntimeError(f"Could not finish bzip2 block at offset {offset}")


def has_game_context(page: dict[str, str]) -> bool:
    value = f"{page['first_paragraph']} {page['categories']}"
    return bool(GAME_CONTEXT_PATTERN.search(value)) or "video games" in value.lower()


def write_header(output: Path) -> tuple[csv.DictWriter, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "document_id",
        "game_title",
        "gamepressure_url",
        "wiki_id",
        "wiki_title",
        "first_paragraph",
        "categories",
        "match_type",
        "xml_part",
        "xml_offset",
    ]
    file = output.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        file,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    return writer, file


def main() -> None:
    args = parse_arguments()
    game_matches = read_game_matches(Path(args.matches))
    selected_pages = read_selected_pages(Path(args.selected_pages), args.limit)
    initial_size = args.block_size_mb * 1024 * 1024
    output = Path(args.output)
    writer, output_file = write_header(output)
    seen: set[tuple[str, str]] = set()
    candidate_count = 0
    confirmed_count = 0

    try:
        for number, selected in enumerate(selected_pages, start=1):
            url = args.base_url + args.dump_name
            xml_bytes = download_range(
                url, int(selected["xml_offset"]), initial_size
            )
            xml_text = xml_bytes.decode("utf-8", errors="replace")
            for page in extract_pages(xml_text):
                games = game_matches.get(page["normalized_title"])
                if games is None:
                    continue
                page["xml_part"] = selected["xml_part"]
                page["xml_offset"] = selected["xml_offset"]
                confirmed = has_game_context(page)
                for game in games:
                    key = (game["document_id"], page["wiki_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    writer.writerow(
                        {
                            "document_id": game["document_id"],
                            "game_title": game["game_title"],
                            "gamepressure_url": game["gamepressure_url"],
                            "wiki_id": page["wiki_id"],
                            "wiki_title": page["wiki_title"],
                            "first_paragraph": page["first_paragraph"],
                            "categories": page["categories"],
                            "match_type": (
                                "confirmed_game_context"
                                if confirmed
                                else "exact_normalized_title_candidate"
                            ),
                            "xml_part": page["xml_part"],
                            "xml_offset": page["xml_offset"],
                        }
                    )
                    candidate_count += 1
                    confirmed_count += int(confirmed)
            if number % 10 == 0 or number == len(selected_pages):
                print(f"Processed blocks: {number}/{len(selected_pages)}")
    finally:
        output_file.close()

    print(f"Candidate blocks: {len(selected_pages)}")
    print(f"Candidate joined documents: {candidate_count}")
    print(f"Confirmed game-context joins: {confirmed_count}")
    print(f"Unique wiki pages: {len({key[1] for key in seen})}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
