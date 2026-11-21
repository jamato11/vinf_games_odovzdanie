import argparse
import csv
import re
import urllib.request
from pathlib import Path

from wiki_parser import normalize_title


PART_PATTERN = re.compile(
    r'href="([^"]*pages-articles-multistream\d+\.xml-p(\d+)p(\d+)\.bz2)"',
    re.IGNORECASE,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find split Wikipedia XML files containing matched game titles."
    )
    parser.add_argument("--matches", required=True, help="wiki_title_matches.tsv")
    parser.add_argument("--index", required=True, help="multistream index .bz2")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--directory-url",
        default="https://dumps.wikimedia.org/enwiki/latest/",
        help="Official dump directory URL",
    )
    return parser.parse_args()


def read_match_titles(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return {
            row["normalized_title"]
            for row in csv.DictReader(file, delimiter="\t")
            if row["title_match"] == "yes"
        }


def read_split_parts(directory_url: str) -> list[tuple[str, int, int]]:
    request = urllib.request.Request(
        directory_url,
        headers={"User-Agent": "FIIT-STU-GameSearch/1.0 (student project)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")
    parts = [
        (match.group(1), int(match.group(2)), int(match.group(3)))
        for match in PART_PATTERN.finditer(html)
    ]
    return sorted(parts, key=lambda part: part[1])


def find_part(parts: list[tuple[str, int, int]], page_id: int) -> str:
    for filename, first_id, last_id in parts:
        if first_id <= page_id <= last_id:
            return filename
    return ""


def scan_index(
    index_path: Path,
    wanted_titles: set[str],
    parts: list[tuple[str, int, int]],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    matches = []
    part_counts: dict[str, int] = {}
    with index_path.open("rb") as binary_file:
        import bz2

        with bz2.open(binary_file, "rt", encoding="utf-8", errors="replace") as file:
            for line in file:
                offset, page_id, title = line.rstrip("\r\n").split(":", 2)
                normalized = normalize_title(title)
                if normalized not in wanted_titles:
                    continue
                filename = find_part(parts, int(page_id))
                if not filename:
                    continue
                matches.append(
                    {
                        "wiki_title": title,
                        "normalized_title": normalized,
                        "wiki_id": page_id,
                        "xml_offset": offset,
                        "xml_part": filename,
                    }
                )
                part_counts[filename] = part_counts.get(filename, 0) + 1
    return matches, part_counts


def write_results(
    output_dir: Path,
    matches: list[dict[str, str]],
    part_counts: dict[str, int],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_path = output_dir / "wiki_selected_pages.tsv"
    with pages_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "wiki_title",
                "normalized_title",
                "wiki_id",
                "xml_offset",
                "xml_part",
            ],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(matches)

    parts_path = output_dir / "wiki_selected_parts.tsv"
    with parts_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        writer.writerow(["xml_part", "matched_titles"])
        for filename, count in sorted(part_counts.items()):
            writer.writerow([filename, count])


def main() -> None:
    args = parse_arguments()
    wanted_titles = read_match_titles(Path(args.matches))
    parts = read_split_parts(args.directory_url)
    matches, part_counts = scan_index(Path(args.index), wanted_titles, parts)
    write_results(Path(args.output_dir), matches, part_counts)
    print(f"Candidate titles: {len(wanted_titles)}")
    print(f"Found in multistream index: {len(matches)}")
    print(f"Required XML parts: {len(part_counts)}")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
