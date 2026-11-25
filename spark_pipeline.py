import argparse
import bz2
import csv
import json
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from wiki_parser import is_game_article, normalize_title, parse_page


BZIP2_STREAM_HEADER_PATTERN = re.compile(rb"BZh[1-9]1AY&SY")
PAGE_XML_BYTES_PATTERN = re.compile(rb"<page\b[^>]*>.*?</page>", re.DOTALL)
PAGE_NAMESPACE_ZERO_BYTES_PATTERN = re.compile(
    rb"<ns\b[^>]*>\s*0\s*</ns>", re.IGNORECASE
)
PAGE_REDIRECT_BYTES_PATTERN = re.compile(rb"<redirect\b[^>]*/?>", re.IGNORECASE)
PAGE_TEXT_BYTES_PATTERN = re.compile(
    rb"<text\b[^>]*>(.*?)</text>", re.IGNORECASE | re.DOTALL
)
PARAGRAPH_BREAK_BYTES_PATTERN = re.compile(rb"\n\s*\n")
CATEGORY_BYTES_PATTERN = re.compile(
    rb"\[\[\s*Category:([^\]|#]+)", re.IGNORECASE
)
BZIP2_SCAN_RANGE_BYTES = 256 * 1024 * 1024
BZIP2_SCAN_BUFFER_BYTES = 4 * 1024 * 1024


def progress(message: str) -> None:
    print(f"[spark_pipeline] {message}", flush=True)


def find_bzip2_stream_headers(
    data: bytes, base_offset: int, range_end: int
) -> list[int]:
    return [
        base_offset + match.start()
        for match in BZIP2_STREAM_HEADER_PATTERN.finditer(data)
        if base_offset + match.start() < range_end
    ]


def scan_bzip2_range(task_range: tuple[str, int, int]) -> Iterator[int]:
    dump_path, range_start, range_end = task_range
    with open(dump_path, "rb") as dump_file:
        chunk_start = range_start
        while chunk_start < range_end:
            chunk_end = min(chunk_start + BZIP2_SCAN_BUFFER_BYTES, range_end)
            dump_file.seek(chunk_start)
            chunk = dump_file.read(
                chunk_end
                - chunk_start
                + len(BZIP2_STREAM_HEADER_PATTERN.pattern)
                - 1
            )
            yield from find_bzip2_stream_headers(chunk, chunk_start, chunk_end)
            chunk_start = chunk_end


def parse_bzip2_member(compressed_member: bytes) -> Iterator[dict[str, str]]:
    xml_bytes = bz2.decompress(compressed_member)
    for match in PAGE_XML_BYTES_PATTERN.finditer(xml_bytes):
        page = parse_game_page(match.group(0))
        if page is not None:
            yield page


def parse_game_page(page_xml: bytes) -> dict[str, str] | None:
    if (
        not PAGE_NAMESPACE_ZERO_BYTES_PATTERN.search(page_xml)
        or PAGE_REDIRECT_BYTES_PATTERN.search(page_xml)
    ):
        return None

    text_match = PAGE_TEXT_BYTES_PATTERN.search(page_xml)
    if text_match is None:
        return None

    wikitext = text_match.group(1)
    paragraph_break = PARAGRAPH_BREAK_BYTES_PATTERN.search(wikitext)
    first_paragraph = (
        wikitext[: paragraph_break.start()]
        if paragraph_break is not None
        else wikitext
    )
    has_game_lead_signal = b"game" in first_paragraph.lower()
    has_game_category_signal = (
        not has_game_lead_signal
        and any(
            b"game" in category.lower()
            for category in CATEGORY_BYTES_PATTERN.findall(wikitext)
        )
    )
    if not has_game_lead_signal and not has_game_category_signal:
        return None

    page = parse_page(page_xml.decode("utf-8"))
    if page is None or not page["normalized_title"] or not is_game_article(page):
        return None
    return page


def parse_dump_blocks(
    block_ranges: Iterable[tuple[int, int]], dump_path: str
) -> Iterator[dict[str, str]]:
    with open(dump_path, "rb") as dump_file:
        for block_start, block_end in block_ranges:
            dump_file.seek(block_start)
            compressed_member = dump_file.read(block_end - block_start)
            if len(compressed_member) != block_end - block_start:
                raise OSError(
                    f"Short read for Wikipedia BZip2 member at byte {block_start}"
                )
            yield from parse_bzip2_member(compressed_member)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan the complete Wikipedia dump, filter game articles, and "
            "outer-join them with Gamepressure documents."
        )
    )
    parser.add_argument("--games", required=True, help="Gamepressure documents.tsv")
    parser.add_argument(
        "--wiki-dump",
        required=True,
        help="Complete Wikipedia pages-articles XML dump, optionally .bz2 compressed",
    )
    parser.add_argument("--output", required=True, help="New output directory")
    parser.add_argument(
        "--partitions",
        type=int,
        default=0,
        help=(
            "Optional parsing partitions; 0 uses 8x Spark's default parallelism "
            "(minimum 32)"
        ),
    )
    return parser.parse_args()


def write_rows(path: Path, fieldnames: list[str], rows) -> int:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=fieldnames,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        count = 0
        for row in rows:
            values = row.asDict(recursive=True) if hasattr(row, "asDict") else row
            writer.writerow(values)
            count += 1
    return count


def write_collision_report(path: Path, collisions) -> int:
    fieldnames = [
        "normalized_title",
        "join_status",
        "gamepressure_count",
        "gamepressure_core_count",
        "wiki_count",
        "gamepressure_candidates",
        "wiki_candidates",
    ]
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        count = 0
        for row in collisions.toLocalIterator():
            game_candidates = row["gamepressure_candidates"] or []
            wiki_candidates = row["wiki_candidates"] or []
            writer.writerow(
                {
                    "normalized_title": row["normalized_title"],
                    "join_status": row["join_status"],
                    "gamepressure_count": row["gamepressure_count"],
                    "gamepressure_core_count": row["gamepressure_core_count"],
                    "wiki_count": row["wiki_count"],
                    "gamepressure_candidates": json.dumps(
                        [candidate.asDict() for candidate in game_candidates],
                        ensure_ascii=False,
                    ),
                    "wiki_candidates": json.dumps(
                        [candidate.asDict() for candidate in wiki_candidates],
                        ensure_ascii=False,
                    ),
                }
            )
            count += 1
    return count


def main() -> None:
    args = parse_arguments()
    if args.partitions < 0:
        raise SystemExit("--partitions must be zero or a positive integer")

    try:
        from pyspark import StorageLevel
        from pyspark.sql import SparkSession, Window
        from pyspark.sql.functions import (
            coalesce,
            col,
            collect_list,
            count,
            lit,
            lower,
            row_number,
            struct,
            sum as spark_sum,
            udf,
            when,
        )
        from pyspark.sql.types import StringType, StructField, StructType
    except ImportError as error:
        raise SystemExit(
            "PySpark is required. Run this script through an environment with Spark."
        ) from error

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    spark = (
        SparkSession.builder.appName("FullWikipediaGameFilterAndOuterJoin")
        .master("local[4]")
        .config("spark.sql.shuffle.partitions", "32")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    try:
        progress("Reading Gamepressure game_detail documents...")
        games_source = (
            spark.read.option("header", True)
            .option("sep", "\t")
            .option("quote", '"')
            .option("escape", '"')
            .csv(args.games)
            .where(col("page_type") == "game_detail")
            .where(col("title").isNotNull())
            .select(
                "document_id",
                "title",
                "url",
                "description",
                "genres",
                "platforms",
                "developer",
                "publisher",
            )
        )
        normalize_udf = udf(normalize_title, StringType())
        games = games_source.withColumn(
            "normalized_title", normalize_udf(col("title"))
        ).persist(StorageLevel.DISK_ONLY)
        game_document_count = games.count()
        progress(f"Gamepressure documents loaded: {game_document_count}")

        progress("Scanning the compressed dump for BZip2 stream boundaries...")
        dump_size = os.path.getsize(args.wiki_dump)
        if dump_size == 0:
            raise ValueError("The Wikipedia dump is empty")
        scan_ranges = [
            (
                args.wiki_dump,
                range_start,
                min(range_start + BZIP2_SCAN_RANGE_BYTES, dump_size),
            )
            for range_start in range(0, dump_size, BZIP2_SCAN_RANGE_BYTES)
        ]
        stream_starts = (
            spark.sparkContext.parallelize(scan_ranges, len(scan_ranges))
            .flatMap(scan_bzip2_range)
            .collect()
        )
        stream_starts.sort()
        if not stream_starts or stream_starts[0] != 0:
            raise ValueError(
                "The Wikipedia dump does not begin with a valid BZip2 stream"
            )
        progress(f"Independent BZip2 streams found: {len(stream_starts)}")
        bzip2_stream_count = len(stream_starts)

        block_ranges = list(
            zip(stream_starts, stream_starts[1:] + [dump_size])
        )
        parse_partitions = args.partitions or max(
            spark.sparkContext.defaultParallelism * 32, 128
        )
        parse_partitions = min(parse_partitions, len(block_ranges))
        page_blocks = spark.sparkContext.parallelize(
            block_ranges, parse_partitions
        )
        wiki_page_rdd = page_blocks.mapPartitions(
            lambda ranges: parse_dump_blocks(ranges, args.wiki_dump)
        )
        wiki_schema = StructType(
            [
                StructField("wiki_id", StringType(), True),
                StructField("wiki_title", StringType(), True),
                StructField("normalized_title", StringType(), True),
                StructField("first_paragraph", StringType(), True),
                StructField("categories", StringType(), True),
            ]
        )
        progress(
            f"Decompressing and filtering all dump pages across "
            f"{parse_partitions} Spark partitions..."
        )
        wiki_articles = spark.createDataFrame(
            wiki_page_rdd, schema=wiki_schema
        ).persist(StorageLevel.DISK_ONLY)
        wiki_game_article_count = wiki_articles.count()
        progress(f"Wikipedia game articles retained by the filter: {wiki_game_article_count}")

        progress("Resolving duplicate title groups and performing a full outer join...")
        game_title_window = Window.partitionBy("normalized_title").orderBy(
            when(
                lower(coalesce(col("url"), lit(""))).contains("addons.asp"),
                lit(1),
            )
            .otherwise(lit(0))
            .asc(),
            col("document_id").asc(),
        )
        ranked_games = games.withColumn(
            "_title_rank", row_number().over(game_title_window)
        )
        primary_games = ranked_games.where(col("_title_rank") == 1).drop("_title_rank")
        game_groups = games.groupBy("normalized_title").agg(
            count("*").alias("gamepressure_count"),
            spark_sum(
                when(
                    ~lower(coalesce(col("url"), lit(""))).contains("addons.asp"),
                    lit(1),
                ).otherwise(lit(0))
            ).alias("gamepressure_core_count"),
            collect_list(
                struct(
                    col("document_id"),
                    col("title"),
                    col("url"),
                    col("description"),
                    col("genres"),
                    col("platforms"),
                    col("developer"),
                    col("publisher"),
                )
            ).alias("gamepressure_candidates"),
        )
        game_groups = game_groups.join(primary_games, "normalized_title").select(
            "normalized_title",
            "gamepressure_count",
            "gamepressure_core_count",
            "gamepressure_candidates",
            "document_id",
            "title",
            "url",
            "description",
            "genres",
            "platforms",
            "developer",
            "publisher",
        )

        wiki_title_window = Window.partitionBy("normalized_title").orderBy(
            col("wiki_title").asc(),
            col("wiki_id").cast("long").asc(),
        )
        ranked_wiki = wiki_articles.withColumn(
            "_title_rank", row_number().over(wiki_title_window)
        )
        primary_wiki = ranked_wiki.where(col("_title_rank") == 1).drop("_title_rank")
        wiki_groups = wiki_articles.groupBy("normalized_title").agg(
            count("*").alias("wiki_count"),
            collect_list(
                struct(
                    col("wiki_id"),
                    col("wiki_title"),
                    col("first_paragraph"),
                    col("categories"),
                )
            ).alias("wiki_candidates"),
        )
        wiki_groups = wiki_groups.join(primary_wiki, "normalized_title").select(
            "normalized_title",
            "wiki_count",
            "wiki_candidates",
            "wiki_id",
            "wiki_title",
            "first_paragraph",
            "categories",
        )

        outer_join = wiki_groups.join(
            game_groups,
            on="normalized_title",
            how="full_outer",
        )
        has_game = col("gamepressure_count").isNotNull()
        has_wiki = col("wiki_count").isNotNull()
        outer_join = outer_join.withColumn(
            "join_status",
            when(
                ~has_game & has_wiki & (col("wiki_count") > 1),
                "collision_wikipedia_only",
            )
            .when(~has_game & has_wiki, "wikipedia_only")
            .when(
                has_game
                & ~has_wiki
                & (col("gamepressure_count") > 1),
                "collision_gamepressure_only",
            )
            .when(has_game & ~has_wiki, "gamepressure_only")
            .when(
                (col("gamepressure_count") == 1) & (col("wiki_count") == 1),
                "matched",
            )
            .when(
                (col("gamepressure_count") > 1)
                & (col("gamepressure_core_count") == 1)
                & (col("wiki_count") == 1),
                "collision_resolved",
            )
            .otherwise("collision_unresolved"),
        ).persist(StorageLevel.DISK_ONLY)

        outer_path = output_dir / "outer_join"
        progress(f"Writing complete outer-join result: {outer_path}")
        outer_join.write.mode("overwrite").parquet(str(outer_path))

        match_rows = outer_join.where(
            col("join_status").isin("matched", "collision_resolved")
        ).select(
            "document_id",
            col("title").alias("game_title"),
            col("url").alias("gamepressure_url"),
            "wiki_id",
            "wiki_title",
            "first_paragraph",
            "categories",
            lit("confirmed_game_context").alias("match_type"),
            "normalized_title",
            "join_status",
        )
        matches_path = output_dir / "matches.tsv"
        progress(f"Writing unambiguous and explicitly resolved matches: {matches_path}")
        match_count = write_rows(
            matches_path,
            [
                "document_id",
                "game_title",
                "gamepressure_url",
                "wiki_id",
                "wiki_title",
                "first_paragraph",
                "categories",
                "match_type",
                "normalized_title",
                "join_status",
            ],
            match_rows.toLocalIterator(),
        )

        collisions = outer_join.where(col("join_status").contains("collision"))
        collision_count = write_collision_report(
            output_dir / "collisions.tsv",
            collisions.select(
                "normalized_title",
                "join_status",
                "gamepressure_count",
                "gamepressure_core_count",
                "wiki_count",
                "gamepressure_candidates",
                "wiki_candidates",
            ),
        )

        progress("Calculating outer-join statistics...")
        status_counts = {
            row["join_status"]: row["count"]
            for row in outer_join.groupBy("join_status").count().collect()
        }
        statistics = {
            "gamepressure_documents": game_document_count,
            "gamepressure_title_keys": game_groups.count(),
            "wikipedia_dump_bytes": dump_size,
            "wikipedia_bzip2_streams": bzip2_stream_count,
            "wikipedia_game_articles": wiki_game_article_count,
            "wikipedia_title_keys": wiki_groups.count(),
            "unique_title_matches": status_counts.get("matched", 0),
            "collision_resolved_matches": status_counts.get(
                "collision_resolved", 0
            ),
            "matches_exported": match_count,
            "unique_matched_wiki_pages": match_rows.select("wiki_id").distinct().count(),
            "gamepressure_only_title_keys": status_counts.get(
                "gamepressure_only", 0
            )
            + status_counts.get("collision_gamepressure_only", 0),
            "wikipedia_only_title_keys": status_counts.get("wikipedia_only", 0)
            + status_counts.get("collision_wikipedia_only", 0),
            "collision_title_keys": collision_count,
            "collision_unresolved_keys": sum(
                value
                for status, value in status_counts.items()
                if status.startswith("collision_")
                and status != "collision_resolved"
            ),
        }
        statistics_path = output_dir / "statistics.tsv"
        write_rows(
            statistics_path,
            list(statistics),
            [statistics],
        )
        for name, value in statistics.items():
            progress(f"{name}: {value}")
        progress("Full-dump processing completed.")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
