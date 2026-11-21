import argparse
from pathlib import Path

from wiki_parser import normalize_title, parse_page


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join Gamepressure game documents with Wikipedia in Spark."
    )
    parser.add_argument("--games", required=True, help="Gamepressure documents.tsv")
    parser.add_argument(
        "--wiki-dump",
        required=True,
        help="Wikipedia pages-articles XML dump, optionally .bz2 compressed",
    )
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--partitions",
        type=int,
        default=0,
        help="Optional number of Spark partitions; 0 keeps the input default",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    try:
        from pyspark.sql import SparkSession
        from pyspark.sql.functions import broadcast, col
    except ImportError as error:
        raise SystemExit(
            "PySpark is required. Run this script through the project Docker image."
        ) from error

    spark = (
        SparkSession.builder.appName("GamepressureWikipediaJoin")
        .master("local[*]")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    games = (
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
    games = games.rdd.map(
        lambda row: row.asDict(recursive=True)
        | {"normalized_title": normalize_title(row["title"])}
    ).toDF()

    wiki_records = spark.sparkContext.newAPIHadoopFile(
        args.wiki_dump,
        "org.apache.hadoop.streaming.StreamInputFormat",
        keyClass="org.apache.hadoop.io.LongWritable",
        valueClass="org.apache.hadoop.io.Text",
        conf={
            "stream.recordreader.class": (
                "org.apache.hadoop.streaming.StreamXmlRecordReader"
            ),
            "stream.recordreader.begin": "<page>",
            "stream.recordreader.end": "</page>",
        },
    )
    if args.partitions > 0:
        wiki_records = wiki_records.repartition(args.partitions)
    wiki_pages = (
        wiki_records.values()
        .map(lambda page_xml: parse_page(page_xml))
        .filter(lambda page: page is not None)
        .toDF()
    )
    wiki_pages = wiki_pages.dropDuplicates(["normalized_title"])

    joined = wiki_pages.join(
        broadcast(games),
        on="normalized_title",
        how="right",
    )
    joined = joined.withColumn(
        "wiki_match",
        col("wiki_title").isNotNull(),
    )
    joined.write.mode("overwrite").option("header", True).parquet(args.output)

    joined.createOrReplaceTempView("joined_games")
    stats = spark.sql(
        """
        SELECT
            COUNT(*) AS game_documents,
            SUM(CASE WHEN wiki_match THEN 1 ELSE 0 END) AS matched_documents,
            COUNT(DISTINCT CASE WHEN wiki_match THEN wiki_title END)
                AS unique_wiki_pages
        FROM joined_games
        """
    )
    stats.write.mode("overwrite").option("header", True).csv(
        str(Path(args.output) / "_statistics")
    )
    stats.show(truncate=False)
    spark.stop()


if __name__ == "__main__":
    main()
