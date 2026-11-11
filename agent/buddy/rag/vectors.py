"""Optional vector side of H3: local e5 embeddings and pgvector storage.

Nothing here is imported at module level from torch, sentence-transformers or psycopg: with
RAG_SEARCH_MODE=bm25 the model is never loaded into memory and the `rag` extra may be absent.
"""

from collections import OrderedDict
from typing import Protocol

from buddy.rag.chunks import Chunk

DIMENSIONS = 384  # intfloat/multilingual-e5-small
SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag_chunks (
    id text PRIMARY KEY,
    commit_sha text NOT NULL,
    file text NOT NULL,
    heading text NOT NULL,
    url text NOT NULL,
    body text NOT NULL,
    embedding vector({DIMENSIONS}) NOT NULL
);
CREATE INDEX IF NOT EXISTS rag_chunks_commit_idx ON rag_chunks(commit_sha);
"""


class VectorUnavailable(Exception):
    """The model, the `rag` extra or the database is not available; callers fall back to BM25."""


class Embedder(Protocol):
    def query(self, text: str) -> list[float]: ...

    def passages(self, texts: list[str]) -> list[list[float]]: ...


class Store(Protocol):
    def search(self, commit: str, vector: list[float], limit: int) -> list[tuple[str, float]]: ...


def literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


class E5Embedder:
    """Lazy sentence-transformers wrapper; the model is loaded on the first call only."""

    def __init__(self, model: str, *, cache: int = 128, loader=None):
        self.name, self.capacity = model, cache
        self._loader = loader
        self._model = None
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self._model is None:
            try:
                if self._loader is not None:
                    self._model = self._loader(self.name)
                else:  # pragma: no cover - needs the optional `rag` extra and the model files
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self.name, device="cpu")
            except Exception as exc:
                raise VectorUnavailable("embedding model") from exc
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = self._load().encode(texts, normalize_embeddings=True, batch_size=16)
        except VectorUnavailable:
            raise
        except Exception as exc:
            raise VectorUnavailable("embedding") from exc
        return [[float(value) for value in vector] for vector in vectors]

    def query(self, text: str) -> list[float]:
        key = text.strip().casefold()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        vector = self._encode(["query: " + text.strip()])[0]  # e5 expects the role prefix
        self._cache[key] = vector
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
        return vector

    def passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(["passage: " + text for text in texts])


class PgVectorStore:
    """`rag_chunks` in the project's PostgreSQL (pgvector image). Short-lived connections."""

    def __init__(self, dsn: str, *, connect=None):
        self.dsn, self._connect = dsn, connect

    def _open(self):
        try:
            if self._connect is not None:
                return self._connect(self.dsn)
            import psycopg  # pragma: no cover - needs the optional `rag` extra

            return psycopg.connect(self.dsn, connect_timeout=5)  # pragma: no cover
        except Exception as exc:
            raise VectorUnavailable("database") from exc

    def _run(self, action):
        connection = self._open()
        try:
            with connection:
                return action(connection)
        except VectorUnavailable:
            raise
        except Exception as exc:
            raise VectorUnavailable("database") from exc
        finally:
            connection.close()

    def replace(self, commit: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """Idempotent: after the call the table holds exactly these fragments (no duplicates)."""
        if len(chunks) != len(vectors) or any(len(vector) != DIMENSIONS for vector in vectors):
            raise ValueError("Every fragment needs one embedding of the model dimension")
        rows = [
            (chunk.id, commit, chunk.file, chunk.heading, chunk.url, chunk.text, literal(vector))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        def action(connection):
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
                cursor.execute("DELETE FROM rag_chunks")
                cursor.executemany(
                    "INSERT INTO rag_chunks (id, commit_sha, file, heading, url, body, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
                    rows,
                )
            return len(rows)

        return self._run(action)

    def count(self, commit: str) -> int:
        def action(connection):
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM rag_chunks WHERE commit_sha = %s", (commit,))
                return int(cursor.fetchone()[0])

        return self._run(action)

    def search(self, commit: str, vector: list[float], limit: int) -> list[tuple[str, float]]:
        value = literal(vector)

        def action(connection):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, 1 - (embedding <=> %s::vector) FROM rag_chunks WHERE commit_sha = %s "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (value, commit, value, limit),
                )
                return [(row[0], float(row[1])) for row in cursor.fetchall()]

        return self._run(action)
