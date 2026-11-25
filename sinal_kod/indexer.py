import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


DATA_DIR = Path("data")
DOCUMENTS_FILE = DATA_DIR / "documents.tsv"
INDEX_DIR = DATA_DIR / "index"
STOPWORDS = {"a", "an", "and", "or", "the", "of", "to", "in", "game", "games"}


class Tokenizer:
    whitespace_pattern = re.compile(r"\s+")
    possessive_pattern = re.compile(r"(['’])s\b", re.IGNORECASE)
    edge_punctuation_pattern = re.compile(
        r"^[^\w@]+|[^\w]+$",
        re.UNICODE,
    )

    @classmethod
    def tokenize(cls, text: str) -> list[str]:
        tokens = []
        for raw_token in cls.whitespace_pattern.split(text.lower()):
            raw_token = cls.possessive_pattern.sub("", raw_token)
            token = cls.edge_punctuation_pattern.sub("", raw_token)
            if token and token not in STOPWORDS:
                tokens.append(token)
        return tokens


class InvertedIndex:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, str]] = {}
        self.postings: dict[str, Counter[str]] = defaultdict(Counter)

    def add_document(self, document: dict[str, str]) -> None:
        document_id = document["document_id"]
        self.documents[document_id] = document
        searchable_text = " ".join(
            document.get(field, "")
            for field in (
                "title",
                "description",
                "genres",
                "platforms",
                "developer",
                "publisher",
                "release_date",
            )
        )
        term_counts = Counter(Tokenizer.tokenize(searchable_text))
        for term, count in term_counts.items():
            self.postings[term][document_id] = count

    def document_frequency(self, term: str) -> int:
        return len(self.postings.get(term, {}))

    def idf_standard(self, term: str) -> float:
        number_of_documents = len(self.documents)
        document_frequency = self.document_frequency(term)
        return math.log(number_of_documents / document_frequency)

    def idf_smoothed(self, term: str) -> float:
        number_of_documents = len(self.documents)
        document_frequency = self.document_frequency(term)
        return math.log(
            (number_of_documents + 1) / (document_frequency + 1)
        ) + 1

    def search(self, query: str, idf_method: str, limit: int = 10) -> list[dict]:
        query_terms = list(dict.fromkeys(Tokenizer.tokenize(query)))
        if not query_terms:
            return []

        posting_lists = [
            self.postings.get(term, {})
            for term in query_terms
        ]
        posting_lists.sort(key=len)
        matching_sets = [set(posting_list) for posting_list in posting_lists]
        if any(not matching for matching in matching_sets):
            return []

        matching_documents = set.intersection(*matching_sets)
        scored = []
        for document_id in matching_documents:
            score = 0.0
            for term in query_terms:
                term_frequency = self.postings[term][document_id]
                idf = (
                    self.idf_standard(term)
                    if idf_method == "standard"
                    else self.idf_smoothed(term)
                )
                score += term_frequency * idf
            scored.append((score, document_id))

        scored.sort(reverse=True)
        return [
            {
                "document_id": document_id,
                "score": round(score, 4),
                "title": self.documents[document_id].get("title", ""),
                "url": self.documents[document_id]["url"],
            }
            for score, document_id in scored[:limit]
        ]

    def save(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "document_map.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n")
            writer.writerow(["document_id", "url", "title"])
            for document_id, document in sorted(self.documents.items()):
                writer.writerow(
                    [document_id, document["url"], document.get("title", "")]
                )

        with (output_dir / "terms.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n")
            writer.writerow(["term", "document_frequency"])
            for term in sorted(self.postings):
                writer.writerow([term, self.document_frequency(term)])

        with (output_dir / "postings.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n")
            writer.writerow(["term", "document_id", "tf"])
            for term in sorted(self.postings):
                for document_id, tf in sorted(self.postings[term].items()):
                    writer.writerow([term, document_id, tf])

        with (output_dir / "idf.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n")
            writer.writerow(["term", "idf_standard", "idf_smoothed"])
            for term in sorted(self.postings):
                writer.writerow(
                    [
                        term,
                        round(self.idf_standard(term), 8),
                        round(self.idf_smoothed(term), 8),
                    ]
                )


def load_index() -> InvertedIndex:
    index = InvertedIndex()
    with DOCUMENTS_FILE.open("r", encoding="utf-8", newline="") as file:
        for document in csv.DictReader(file, delimiter="\t"):
            if document.get("page_type") == "game_detail":
                index.add_document(document)
    return index


def main() -> None:
    index = load_index()
    index.save(INDEX_DIR)
    print(f"Indexed documents: {len(index.documents)}")
    print(f"Unique terms: {len(index.postings)}")

    while True:
        query = input("\nquery: ").strip()
        if not query or query.lower() in {"quit", "exit"}:
            break
        for method in ("standard", "smoothed"):
            results = index.search(query, method)
            print(f"\nIDF method: {method}")
            for result in results:
                print(
                    f"{result['score']:.4f}\t"
                    #f"{result['document_id']}\t"
                    f"{result['title']}\t"
                    #f"{result['url']}"
                )


if __name__ == "__main__":
    main()
