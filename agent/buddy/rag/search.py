"""Playbook search in one of three modes chosen by RAG_SEARCH_MODE.

- bm25   — lexical only; no model in memory, no database (default, demo, CI, weak machines);
- vector — e5 query embedding + pgvector nearest fragments;
- rrf    — reciprocal rank fusion of both rankings.

Vector modes embed only the question at run time (fragments are embedded once by `make ingest`).
If the model or the database is unavailable the search silently degrades to BM25 (hard rule 3).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

from buddy.rag.index import Hit, PlaybookIndex, default_index, search_query
from buddy.rag.vectors import Embedder, Store, VectorUnavailable

Mode = Literal["bm25", "vector", "rrf"]
RRF_K = 60
CANDIDATES = 20
log = logging.getLogger(__name__)


@dataclass
class Found:
    hits: list[Hit]
    answerable: bool
    mode: Mode
    degraded: bool = False


@dataclass
class PlaybookSearch:
    index: PlaybookIndex
    mode: Mode = "bm25"
    embedder: Embedder | None = None
    store: Store | None = None
    min_similarity: float = 0.82
    warned: bool = field(default=False, repr=False)

    def _lexical(self, query: str, limit: int) -> Found:
        hits = self.index.search(query, limit)
        return Found(hits, self.index.answerable(query, hits), "bm25")

    def _nearest(self, query: str) -> list[tuple[int, float]]:
        if self.embedder is None or self.store is None:
            raise VectorUnavailable("not configured")
        found = self.store.search(self.index.commit, self.embedder.query(query), CANDIDATES)
        known = [(self.index.by_id[item], score) for item, score in found if item in self.index.by_id]
        if not known:
            raise VectorUnavailable("index is empty; run make ingest")
        return known

    def _search(self, query: str, limit: int) -> Found:
        if self.mode == "bm25":
            return self._lexical(query, limit)
        try:
            nearest = self._nearest(query)
        except VectorUnavailable as exc:
            if not self.warned:
                log.warning("Vector search unavailable (%s); using BM25", exc)
                self.warned = True
            found = self._lexical(query, limit)
            found.degraded = True
            return found
        # Refusal (FR-H3-03): a word the playbook never uses (password, VPN) or a weak best match.
        # The threshold is tuned on evals/h3: in-corpus questions score 0.82–0.94 with e5-small.
        similar = nearest[0][1] >= self.min_similarity and self.index.known(query)
        if self.mode == "vector":
            hits = self.index.hits([(number, score, score) for number, score in nearest], limit)
            return Found(hits, similar, "vector")
        lexical = self.index.rank(query, CANDIDATES)
        coverage = {number: value for number, _, value in lexical}
        fused: dict[int, float] = {}
        for ranking in ([number for number, _, _ in lexical], [number for number, _ in nearest]):
            for place, number in enumerate(ranking):
                fused[number] = fused.get(number, 0.0) + 1 / (RRF_K + place + 1)
        order = sorted(fused, key=lambda number: (-fused[number], number))
        hits = self.index.hits(
            [(number, fused[number], coverage.get(number, 0.0)) for number in order], limit
        )
        return Found(hits, bool(hits) and similar, "rrf")

    async def search(self, query: str, limit: int = 3) -> Found:
        query = search_query(query)
        if self.mode == "bm25":
            return self._search(query, limit)
        # Model inference and the database call are blocking: keep the bot's event loop free.
        return await asyncio.to_thread(self._search, query, limit)


def create_search(settings, index: PlaybookIndex | None = None) -> PlaybookSearch:
    """Build the search for the configured mode; heavy objects are created lazily on first use."""
    index = index or default_index()
    mode = settings.rag_search_mode
    dsn = settings.rag_database_url.get_secret_value()
    if mode == "bm25" or not dsn:
        if mode != "bm25":
            log.warning("RAG_SEARCH_MODE=%s needs RAG_DATABASE_URL; using BM25", mode)
        return PlaybookSearch(index, "bm25")
    from buddy.rag.vectors import E5Embedder, PgVectorStore

    return PlaybookSearch(
        index,
        mode,
        embedder=E5Embedder(settings.embeddings_model),
        store=PgVectorStore(dsn),
        min_similarity=settings.rag_min_similarity,
    )
