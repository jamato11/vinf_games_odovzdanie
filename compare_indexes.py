import argparse
from pathlib import Path

from indexer import load_index
from lucene_indexer import search_index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the old TF-IDF index with the PyLucene index."
    )
    parser.add_argument("query")
    parser.add_argument("--lucene-index", default="data/lucene_index")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    old_index = load_index()
    old_results = old_index.search(args.query, "smoothed", args.limit)
    new_results = search_index(
        Path(args.lucene_index),
        args.query,
        "boolean",
        args.limit,
    )

    print("OLD INDEX (custom TF-IDF AND search)")
    for result in old_results:
        print(
            f"{result['score']}\t{result['document_id']}\t"
            f"{result['title']}"
        )
    print("\nNEW INDEX (PyLucene Boolean search)")
    for result in new_results:
        print(
            f"{result['score']}\t{result['document_id']}\t"
            f"{result['title']}"
        )


if __name__ == "__main__":
    main()
