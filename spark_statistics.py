import argparse
import csv
import os
import sys
from pathlib import Path


def progress(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Spark statistics from the completed wiki join."
    )
    parser.add_argument("--joined", required=True, help="joined_matches.tsv")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--write-parquet",
        action="store_true",
        help="Also write Parquet; on Windows this requires winutils.exe",
    )
    args = parser.parse_args()

    try:
        from pyspark.sql import SparkSession
        from pyspark.sql.functions import (
            col,
            concat_ws,
            countDistinct,
            sum as spark_sum,
            when,
        )
    except ImportError as error:
        raise SystemExit("PySpark is required.") from error

    progress("Starting Spark...")
    progress(f"Python: {sys.executable}")
    progress(f"JAVA_HOME: {os.environ.get('JAVA_HOME', '<not set>')}")
    progress("Creating local Spark session...")
    spark = (
        SparkSession.builder.appName("GamepressureWikipediaStatistics")
        .master("local[*]")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    progress("Spark session ready.")

    progress(f"Reading TSV: {args.joined}")
    joined = (
        spark.read.option("header", True)
        .option("sep", "\t")
        .option("quote", '"')
        .option("escape", '"')
        .csv(args.joined)
    )
    progress(f"Input rows: {joined.count()}")
    progress("Adding match classification...")
    game_context_pattern = (
        r"(?i)(video game|computer game|mobile game|role-playing game|"
        r"action-adventure game|strategy game|simulation game|"
        r"game developed|game published|game by|video games)"
    )
    joined = joined.withColumn(
        "wiki_search_text",
        concat_ws(" ", col("first_paragraph"), col("categories")),
    )
    joined = joined.withColumn(
        "confirmed",
        col("wiki_search_text").rlike(game_context_pattern),
    )
    statistics = joined.agg(
        countDistinct("document_id").alias("joined_game_documents"),
        countDistinct("wiki_id").alias("unique_wiki_pages"),
        spark_sum(col("confirmed").cast("int")).alias("confirmed_join_rows"),
        countDistinct(
            when(col("confirmed"), col("document_id"))
        ).alias("confirmed_game_documents"),
        countDistinct(
            when(col("confirmed"), col("wiki_id"))
        ).alias("confirmed_unique_wiki_pages"),
    )
    output = Path(args.output)
    progress("Calculating statistics...")
    statistics.show(truncate=False)
    output.mkdir(parents=True, exist_ok=True)
    values = statistics.collect()[0].asDict()
    statistics_path = output / "statistics.tsv"
    with statistics_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(values),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(values)
    progress(f"Statistics written: {statistics_path}")
    if args.write_parquet:
        progress(f"Writing Parquet: {output / 'joined'}")
        joined.write.mode("overwrite").parquet(str(output / "joined"))
    progress("Spark processing completed.")
    spark.stop()


if __name__ == "__main__":
    main()
