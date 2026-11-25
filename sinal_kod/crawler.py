import csv
import hashlib
import os
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from link_extractor import LinkExtractor


BASE_URL = "https://www.gamepressure.com"
SEED_URLS = [
    f"{BASE_URL}/games/",
    f"{BASE_URL}/games/upcoming/",
    f"{BASE_URL}/games/pc/",
    f"{BASE_URL}/games/xbox/",
    f"{BASE_URL}/games/playstation/",
    f"{BASE_URL}/games/nintendo/",
    f"{BASE_URL}/games/rpg/",
    f"{BASE_URL}/games/action/",
    f"{BASE_URL}/games/strategy/",
    f"{BASE_URL}/games/adventure/",
]
DATA_DIR = Path("data")
URLS_FILE = DATA_DIR / "urls.tsv"
FAILED_FILE = DATA_DIR / "failed_urls.tsv"
HTML_DIR = DATA_DIR / "html"
PAGES_FILE = DATA_DIR / "pages.tsv"
URL_HASH_LENGTH = 12

# Maximum number of pages downloaded in one run. The crawler can be stopped
# and started again; queued URLs remain in data/urls.tsv.
# The process may run for hours. Ctrl+C is safe because every URL is written
# immediately and queued URLs are restored on the next start.
MAX_PAGES_PER_RUN = 5000
TARGET_DOWNLOADED_PAGES = 6821
TIMEOUT_SECONDS = 20
SLEEP_SECONDS = 3
MAX_BACKOFF_SECONDS = 120

# The browser-like headers are used together with a responsible delay.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36 "
        "FIIT-STU-GameSearch/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Page:
    url: str
    html: str
    status_code: int
    content_type: str


class PageDownloader:
    """Downloads HTML only. It does not extract links or text."""

    def __init__(self) -> None:
        self.session = requests.Session()

    def download(self, url: str) -> Page:
        response = self.session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise ValueError(f"Not an HTML page: {content_type}")
        return Page(
            url=url,
            html=response.text,
            status_code=response.status_code,
            content_type=content_type,
        )


class PageStorage:
    """Stores downloaded HTML so it can be parsed again without refetching."""

    def __init__(self, html_dir: Path, pages_file: Path) -> None:
        self.html_dir = html_dir
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.pages_file = pages_file
        file_exists = (
            self.pages_file.exists() and self.pages_file.stat().st_size > 0
        )
        self.file = self.pages_file.open("a", encoding="utf-8", newline="")
        self.writer = csv.writer(
            self.file, delimiter="\t", lineterminator="\n"
        )
        if not file_exists:
            self.writer.writerow(
                [
                    "url",
                    "html_file",
                    "downloaded_at",
                    "http_status",
                    "content_type",
                    "bytes",
                ]
            )
        self.file.flush()

    def save(self, page: Page) -> None:
        url_hash = hashlib.sha1(page.url.encode("utf-8")).hexdigest()[
            :URL_HASH_LENGTH
        ]
        html_path = self.html_dir / f"{url_hash}.html"
        html_path.write_text(page.html, encoding="utf-8")
        self.writer.writerow(
            [
                page.url,
                str(html_path),
                datetime.now(timezone.utc).isoformat(),
                page.status_code,
                page.content_type,
                len(page.html.encode("utf-8")),
            ]
        )
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class UrlFile:
    """Stores one current-status row for every discovered URL."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = self.read_latest(path)

    @staticmethod
    def read_latest(path: Path) -> dict[str, dict[str, str]]:
        if not path.exists():
            return {}
        latest: dict[str, dict[str, str]] = {}
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file, delimiter="\t"):
                if row.get("url"):
                    latest[row["url"]] = row
        return latest

    def add(
        self,
        url: str,
        url_type: str,
        discovered_from: str,
        depth: int,
        status: str,
    ) -> None:
        self.rows[url] = {
            "url": url,
            "url_type": url_type,
            "discovered_from": discovered_from,
            "depth": str(depth),
            "status": status,
        }
        self.write_rows()

    def write_rows(self) -> None:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.path.parent,
                prefix="urls_",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_path = Path(file.name)
                writer = csv.writer(
                    file, delimiter="\t", lineterminator="\n"
                )
                writer.writerow(
                    ["url", "url_type", "discovered_from", "depth", "status"]
                )
                for row in self.rows.values():
                    writer.writerow(
                        [
                            row["url"],
                            row["url_type"],
                            row["discovered_from"],
                            row["depth"],
                            row["status"],
                        ]
                    )

            last_error = None
            for attempt in range(5):
                try:
                    os.replace(temporary_path, self.path)
                    return
                except PermissionError as error:
                    last_error = error
                    if attempt < 4:
                        time.sleep(0.5)
            raise PermissionError(
                f"Could not replace {self.path}. Close the file in another "
                f"program and try again."
            ) from last_error
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def close(self) -> None:
        pass


class Crawler:
    def __init__(
        self,
        seed_urls: list[str],
        max_pages_per_run: int,
        target_downloaded_pages: int,
    ) -> None:
        latest = UrlFile.read_latest(URLS_FILE)
        self.frontier = deque()
        self.discovered = set(latest)
        self.existing_urls = set(latest)
        for row in latest.values():
            if row["status"] == "queued":
                self.frontier.append((row["url"], int(row["depth"])))
        for url in seed_urls:
            if url not in self.discovered:
                self.frontier.append((url, 0))
                self.discovered.add(url)
        self.downloader = PageDownloader()
        self.url_file = UrlFile(URLS_FILE)
        self.page_storage = PageStorage(HTML_DIR, PAGES_FILE)
        self.max_pages_per_run = max_pages_per_run
        self.target_downloaded_pages = target_downloaded_pages
        self.seed_urls = seed_urls

    def run(self) -> None:
        downloaded_pages = 0
        delay_seconds = SLEEP_SECONDS
        try:
            for seed_url in self.seed_urls:
                if seed_url not in self.existing_urls:
                    self.url_file.add(seed_url, "seed", "", 0, "queued")

            while (
                self.frontier
                and downloaded_pages < self.max_pages_per_run
                and self.count_downloaded_pages() < self.target_downloaded_pages
            ):
                url, depth = self.frontier.popleft()
                print(
                    f"[{downloaded_pages + 1}/{self.max_pages_per_run}] "
                    f"Downloading {url}"
                )
                try:
                    page = self.downloader.download(url)
                    self.page_storage.save(page)
                    self.url_file.add(
                        url, self.url_type(url), "", depth, "downloaded"
                    )
                    links = LinkExtractor.extract_links(page.html, page.url)
                    downloaded_pages += 1
                    new_links = self.add_links(links, url, depth)
                    delay_seconds = SLEEP_SECONDS
                    print(
                        f"  found {len(links)} links, "
                        f"added {new_links} new URLs"
                    )
                except requests.HTTPError as error:
                    status_code = (
                        str(error.response.status_code)
                        if error.response is not None
                        else ""
                    )
                    self.url_file.add(
                        url, self.url_type(url), "", depth, "failed"
                    )
                    self.save_failure(
                        url, status_code, str(error)
                    )
                    if status_code in {"403", "429", "500", "502", "503", "504"}:
                        delay_seconds = self.get_backoff_seconds(error)
                    print(f"  failed ({status_code}): {error}")
                except requests.Timeout as error:
                    self.url_file.add(
                        url, self.url_type(url), "", depth, "failed"
                    )
                    self.save_failure(url, "TIMEOUT", str(error))
                    delay_seconds = min(
                        delay_seconds * 2, MAX_BACKOFF_SECONDS
                    )
                    print(f"  failed (TIMEOUT): {error}")
                except requests.RequestException as error:
                    self.url_file.add(
                        url, self.url_type(url), "", depth, "failed"
                    )
                    self.save_failure(url, "REQUEST_ERROR", str(error))
                    delay_seconds = min(
                        delay_seconds * 2, MAX_BACKOFF_SECONDS
                    )
                    print(f"  failed: {error}")
                except ValueError as error:
                    self.url_file.add(
                        url, self.url_type(url), "", depth, "failed"
                    )
                    self.save_failure(url, "NOT_HTML", str(error))
                    print(f"  failed (NOT_HTML): {error}")

                if (
                    self.frontier
                    and downloaded_pages < self.max_pages_per_run
                    and self.count_downloaded_pages() < self.target_downloaded_pages
                ):
                    print(f"  waiting {delay_seconds} seconds")
                    time.sleep(delay_seconds)
        finally:
            self.url_file.close()
            self.page_storage.close()

        print(f"Finished. Downloaded {downloaded_pages} pages this run.")
        print(f"URLs waiting in frontier: {len(self.frontier)}")
        print(f"Downloaded HTML pages in total: {self.count_downloaded_pages()}")
        print(f"Known candidate URLs: {self.count_candidates()}")
        if self.count_downloaded_pages() >= self.target_downloaded_pages:
            print("Target number of downloaded HTML pages reached.")

    @staticmethod
    def get_backoff_seconds(error: requests.HTTPError) -> int:
        response = error.response
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return min(int(retry_after), MAX_BACKOFF_SECONDS)
        return min(SLEEP_SECONDS * 2, MAX_BACKOFF_SECONDS)

    def add_links(
        self,
        links: list[str],
        source_url: str,
        source_depth: int,
    ) -> int:
        added = 0
        for url in links:
            url_type = self.url_type(url)
            if url_type == "wikipedia":
                self.page_storage.save(page)
                self.url_file.add(
                    url, url_type, source_url, source_depth + 1, "found"
                )
                continue
            if url_type == "ignore" or url in self.discovered:
                continue

            self.discovered.add(url)
            self.frontier.append((url, source_depth + 1))
            self.url_file.add(
                url, url_type, source_url, source_depth + 1, "queued"
            )
            added += 1
        return added

    def count_candidates(self) -> int:
        latest = UrlFile.read_latest(URLS_FILE)
        return sum(
            row["url_type"] in {"seed", "candidate", "overview", "game_detail"}
            for row in latest.values()
        )

    @staticmethod
    def count_downloaded_pages() -> int:
        latest = UrlFile.read_latest(URLS_FILE)
        return sum(row["status"] == "downloaded" for row in latest.values())

    @staticmethod
    def url_type(url: str) -> str:
        parsed = urlparse(url)
        if parsed.netloc.lower() == "en.wikipedia.org":
            return "wikipedia"
        if parsed.netloc.lower() != urlparse(BASE_URL).netloc:
            return "ignore"
        if parsed.path != "/games" and not parsed.path.startswith("/games/"):
            return "ignore"
        if parsed.path.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js")
        ):
            return "ignore"
        return "candidate"

    @staticmethod
    def save_failure(url: str, error_code: str, error: str) -> None:
        FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_exists = FAILED_FILE.exists()
        with FAILED_FILE.open("a", encoding="utf-8") as file:
            if not file_exists:
                file.write("timestamp\thttp_status\turl\terror\n")
            timestamp = datetime.now(timezone.utc).isoformat()
            file.write(f"{timestamp}\t{error_code}\t{url}\t{error}\n")


def main() -> None:
    Crawler(
        SEED_URLS,
        MAX_PAGES_PER_RUN,
        TARGET_DOWNLOADED_PAGES,
    ).run()


if __name__ == "__main__":
    main()
