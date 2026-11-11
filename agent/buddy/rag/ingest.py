"""Index a playbook snapshot: `python -m buddy.rag.ingest` (make ingest).

Deterministic and idempotent: the same snapshot and commit always produce the same file (FR-H3-01).
"""

import argparse
import json
from pathlib import Path

from buddy.config import Settings
from buddy.rag.chunks import Chunk, split_snapshot
from buddy.rag.index import INDEX
from buddy.rag.vectors import E5Embedder, PgVectorStore, VectorUnavailable

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "data" / "playbook" / "snapshot"
COMMIT = "0c1d2aa1501ca6889357908f54b12695f0534e48"


def build(source: Path = SNAPSHOT, commit: str = COMMIT) -> dict:
    chunks = split_snapshot(source, commit)
    if not chunks:
        raise ValueError("Playbook snapshot is empty")
    if len({chunk.id for chunk in chunks}) != len(chunks):
        raise ValueError("Duplicate fragment IDs")
    return {
        "source": "https://github.com/avito-tech/playbook",
        "commit": commit,
        "files": len({chunk.file for chunk in chunks}),
        "chunks": [chunk.as_dict() for chunk in chunks],
    }


def write(index: dict, output: Path = INDEX) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def embed(index: dict, embedder, store, batch: int = 32) -> int:
    """Embed every fragment once and replace `rag_chunks`; the run is repeatable without duplicates."""
    chunks = [Chunk(**item) for item in index["chunks"]]
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch):
        part = chunks[start : start + batch]
        vectors.extend(embedder.passages([chunk.heading + "\n" + chunk.text for chunk in part]))
    return store.replace(index["commit"], chunks, vectors)


def main(argv=None, *, settings=None, embedder=None, store=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SNAPSHOT)
    parser.add_argument("--commit", default=COMMIT)
    parser.add_argument("--output", type=Path, default=INDEX)
    parser.add_argument(
        "--embed",
        choices=["auto", "yes", "no"],
        default="auto",
        help="write e5 embeddings to pgvector; auto = only when RAG_SEARCH_MODE is vector or rrf",
    )
    parser.add_argument(
        "--embed-only",
        action="store_true",
        help="embed the bundled index without the snapshot (inside the agent container)",
    )
    args = parser.parse_args(argv)
    if args.embed_only:
        index = json.loads(args.output.read_text(encoding="utf-8"))
        args.embed = "yes"
    else:
        index = build(args.source, args.commit)
        write(index, args.output)
    print(f"Indexed {len(index['chunks'])} fragments from {index['files']} files at {index['commit'][:7]}")
    settings = settings or Settings()
    wanted = args.embed == "yes" or (args.embed == "auto" and settings.rag_search_mode != "bm25")
    if not wanted:
        print("Embeddings skipped: RAG_SEARCH_MODE=bm25 needs no model and no database")
        return 0
    dsn = settings.rag_database_url.get_secret_value()
    if not dsn and store is None:
        print("Embeddings need RAG_DATABASE_URL (PostgreSQL with pgvector)")
        return 2
    try:
        count = embed(index, embedder or E5Embedder(settings.embeddings_model), store or PgVectorStore(dsn))
    except VectorUnavailable as exc:
        print(f"Embeddings failed: {exc} is unavailable (install the `rag` extra, check the database)")
        return 2
    print(f"Stored {count} embeddings in rag_chunks ({settings.embeddings_model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
