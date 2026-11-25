import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


DATA_DIR = Path("data")
DOCUMENTS_FILE = DATA_DIR / "documents.tsv"
WIKI_JOIN_FILE = DATA_DIR / "wiki_full_scan" / "matches.tsv"
DEFAULT_INDEX_DIR = DATA_DIR / "lucene_index"


SEARCH_FIELDS = [
    "title",
    "description",
    "genres",
    "platforms",
    "developer",
    "publisher",
    "wiki_title",
    "wiki_summary",
    "wiki_categories",
]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and query the Gamepressure PyLucene index."
    )
    parser.add_argument("--documents", default=str(DOCUMENTS_FILE))
    parser.add_argument("--wiki-join", default=str(WIKI_JOIN_FILE))
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--query")
    parser.add_argument(
        "--query-type",
        choices=["boolean", "phrase", "range", "fuzzy"],
        default="boolean",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


def load_wiki_data(path: Path) -> dict[str, dict[str, str]]:
    wiki_by_document: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            if row.get("match_type") != "confirmed_game_context":
                continue
            document_id = row["document_id"]
            current = wiki_by_document.setdefault(
                document_id,
                {
                    "wiki_title": row.get("wiki_title", ""),
                    "wiki_summary": row.get("first_paragraph", ""),
                    "wiki_categories": row.get("categories", ""),
                    "wiki_id": row.get("wiki_id", ""),
                },
            )
            if len(row.get("first_paragraph", "")) > len(current["wiki_summary"]):
                current["wiki_summary"] = row["first_paragraph"]
    return wiki_by_document


def load_documents(
    documents_path: Path,
    wiki_path: Path,
) -> list[dict[str, str]]:
    wiki_by_document = load_wiki_data(wiki_path)
    documents = []
    with documents_path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            if row.get("page_type") != "game_detail":
                continue
            wiki = wiki_by_document.get(row["document_id"], {})
            row.update(
                {
                    "wiki_title": wiki.get("wiki_title", ""),
                    "wiki_summary": wiki.get("wiki_summary", ""),
                    "wiki_categories": wiki.get("wiki_categories", ""),
                    "wiki_id": wiki.get("wiki_id", ""),
                }
            )
            documents.append(row)
    return documents


def add_document_fields(document: dict[str, str]):
    from org.apache.lucene.document import (
        Document,
        Field,
        IntPoint,
        StoredField,
        StringField,
        TextField,
    )

    indexed = Document()
    indexed.add(StringField("document_id", document["document_id"], Field.Store.YES))
    indexed.add(StoredField("url", document.get("url", "")))

    for field in SEARCH_FIELDS:
        value = document.get(field, "").strip()
        indexed.add(TextField(field, value, Field.Store.YES))

    indexed.add(
        StringField("page_type", document.get("page_type", ""), Field.Store.YES)
    )
    indexed.add(
        StringField("wiki_id", document.get("wiki_id", ""), Field.Store.YES)
    )
    release_year = re.match(r"\d{2}-\d{2}-(\d{4})$", document.get("release_date", ""))
    if release_year:
        indexed.add(IntPoint("release_year", int(release_year.group(1))))
        indexed.add(
            StoredField("release_year", int(release_year.group(1)))
        )
    indexed.add(
        StoredField("release_date", document.get("release_date", ""))
    )
    return indexed


def open_lucene():
    import lucene

    if lucene.getVMEnv() is None:
        lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    from java.nio.file import Paths
    from org.apache.lucene.analysis.standard import StandardAnalyzer
    from org.apache.lucene.store import FSDirectory

    directory = FSDirectory.open(Paths.get(str(DEFAULT_INDEX_DIR)))
    analyzer = StandardAnalyzer()
    return directory, analyzer


def build_index(documents: list[dict[str, str]], index_dir: Path) -> int:
    import lucene
    from java.nio.file import Paths
    from org.apache.lucene.analysis.standard import StandardAnalyzer
    from org.apache.lucene.index import IndexWriter, IndexWriterConfig
    from org.apache.lucene.store import FSDirectory

    lucene.getVMEnv() or lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    index_dir.mkdir(parents=True, exist_ok=True)
    directory = FSDirectory.open(Paths.get(str(index_dir)))
    config = IndexWriterConfig(StandardAnalyzer())
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    writer = IndexWriter(directory, config)
    try:
        for document in documents:
            writer.updateDocument(
                __import__("org.apache.lucene.index", fromlist=["Term"]).Term(
                    "document_id", document["document_id"]
                ),
                add_document_fields(document),
            )
        writer.commit()
        return len(documents)
    finally:
        writer.close()
        directory.close()


def make_query(query_text: str, query_type: str, analyzer):
    from org.apache.lucene.index import Term
    from org.apache.lucene.search import (
        BooleanClause,
        BooleanQuery,
        FuzzyQuery,
        MatchAllDocsQuery,
    )
    from org.apache.lucene.queryparser.classic import QueryParser

    if query_type == "phrase":
        parser = QueryParser("description", analyzer)
        return parser.parse(f'"{query_text}"')
    if query_type == "range":
        try:
            start, end = [int(part.strip()) for part in query_text.split(":", 1)]
        except (ValueError, IndexError) as error:
            raise ValueError(
                "Range query must use two years, for example 2020:2024"
            ) from error
        from org.apache.lucene.document import IntPoint

        return IntPoint.newRangeQuery("release_year", start, end)
    if query_type == "fuzzy":
        return FuzzyQuery(Term("title", query_text.strip().lower()), 2)

    builder = BooleanQuery.Builder()
    for field in ("title", "description", "genres", "platforms", "wiki_summary"):
        parser = QueryParser(field, analyzer)
        parsed = parser.parse(query_text)
        builder.add(parsed, BooleanClause.Occur.SHOULD)
    return builder.build() if query_text.strip() else MatchAllDocsQuery()


def search_index(
    index_dir: Path,
    query_text: str,
    query_type: str,
    limit: int,
) -> list[dict[str, str | float]]:
    import lucene
    from java.nio.file import Paths
    from org.apache.lucene.analysis.standard import StandardAnalyzer
    from org.apache.lucene.index import DirectoryReader
    from org.apache.lucene.search import IndexSearcher
    from org.apache.lucene.store import FSDirectory

    lucene.getVMEnv() or lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    directory = FSDirectory.open(Paths.get(str(index_dir)))
    analyzer = StandardAnalyzer()
    reader = DirectoryReader.open(directory)
    try:
        searcher = IndexSearcher(reader)
        stored_fields = searcher.storedFields()
        query = make_query(query_text, query_type, analyzer)
        top_docs = searcher.search(query, limit)
        results = []
        for hit in top_docs.scoreDocs:
            document = stored_fields.document(hit.doc)
            results.append(
                {
                    "document_id": document.get("document_id"),
                    "title": document.get("title"),
                    "url": document.get("url"),
                    "score": round(float(hit.score), 4),
                }
            )
        return results
    finally:
        reader.close()
        directory.close()


def print_results(results: list[dict[str, str | float]]) -> None:
    for result in results:
        print(
            f"{result['score']}\t{result['document_id']}\t"
            f"{result['title']}\t{result['url']}"
        )


def main() -> None:
    args = parse_arguments()
    if args.rebuild or not Path(args.index_dir).exists():
        documents = load_documents(Path(args.documents), Path(args.wiki_join))
        count = build_index(documents, Path(args.index_dir))
        print(f"Indexed documents: {count}")
    if args.query:
        print_results(
            search_index(
                Path(args.index_dir),
                args.query,
                args.query_type,
                args.limit,
            )
        )


if __name__ == "__main__":
    main()
