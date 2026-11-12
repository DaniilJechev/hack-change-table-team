"""Lexical playbook index: BM25 over heading-level fragments, refusal by query coverage.

Needs no model, no database and no network: it is the default search mode (RAG_SEARCH_MODE=bm25),
the engine of the offline demo and the fallback of the vector modes (see buddy.rag.search).
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from buddy.rag.chunks import Chunk

INDEX = Path(__file__).with_name("data") / "index.json"
STOP = frozenset(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне "
    "было вот от меня еще нет о из ему теперь когда даже ну ли если уже или ни быть был него до вас "
    "нибудь вам там чем себя для мы их это этот эта эти такое такой какой какая какие какое есть "
    "нужно можно ли при про над под чтобы кто где куда зачем почему сколько такие чего which the a an "
    "of to in is are what how авито каком какого какому каким каких какую "
    # Request verbs carry no topic: «расскажи» otherwise matches a book title (B-01).
    "расскажи расскажите объясни объясните подскажи подскажите скажи скажите".split()
)
ENDINGS = (
    "иями ями ами ого его ому ему ыми ими ией ется ится атся ятся ются утся ить ать ять еть ишь ешь "
    "ей ой ый ий ая яя ое ее ые ие ых их ую юю ов ев ам ям ом ем им ах ях ию ия ью ть ти ет ут ют ат ят "
    "ит а я ы и о е у ю ь s".split()
)
# Colloquial Russian names of roles and practices that the playbook writes in English.
SYNONYMS = {"техлид": "tech lead", "тимлид": "team lead", "ревью": "review", "скрам": "scrum"}
K1, B = 1.4, 0.7
MIN_COVERAGE = 0.4


def stem(word: str) -> str:
    for ending in ENDINGS:
        if len(word) - len(ending) >= 3 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-zа-я0-9]+", text.casefold().replace("ё", "е"))
    words = [part for word in words for part in SYNONYMS.get(word, word).split()]
    return [stem(word) for word in words if word not in STOP and len(word) > 1]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    coverage: float


class PlaybookIndex:
    def __init__(self, chunks: list[Chunk], *, commit: str = ""):
        self.chunks, self.commit = chunks, commit
        self.by_id = {chunk.id: number for number, chunk in enumerate(chunks)}
        # Heading terms are weighted: a section title is the best summary of its text.
        self.docs = [Counter(tokens(chunk.heading) * 3 + tokens(chunk.text)) for chunk in chunks]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        frequency = Counter(term for doc in self.docs for term in doc)
        total = len(chunks)
        self.idf = {term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in frequency.items()}
        self.unknown = math.log(1 + (total + 0.5) / 0.5) if total else 1.0
        # Prefixes of the vocabulary: a crude stemmer leaves word forms that differ only in the ending.
        self.prefixes = {term[:size] for term in self.idf for size in range(4, len(term) + 1)}

    @classmethod
    def load(cls, path: Path = INDEX) -> "PlaybookIndex":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([Chunk(**item) for item in data["chunks"]], commit=data["commit"])

    def rank(self, query: str, limit: int = 20) -> list[tuple[int, float, float]]:
        """Best fragments as (chunk number, BM25 score, share of the query weight they cover)."""
        terms = list(dict.fromkeys(tokens(query)))
        if not terms or not self.chunks:
            return []
        weight = {term: self.idf.get(term, self.unknown) for term in terms}
        total = sum(weight.values())
        scored = []
        for number, doc in enumerate(self.docs):
            matched = [term for term in terms if term in doc]
            if not matched:
                continue
            norm = K1 * (1 - B + B * self.lengths[number] / self.average)
            score = sum(weight[t] * doc[t] * (K1 + 1) / (doc[t] + norm) for t in matched)
            scored.append((number, score, sum(weight[t] for t in matched) / total))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def known(self, query: str) -> bool:
        """False when the question has a word that the whole playbook never uses."""
        return all(
            term in self.idf or (len(term) >= 5 and term[: max(4, len(term) - 2)] in self.prefixes)
            for term in tokens(query)
        )

    def hits(self, ranked: list[tuple[int, float, float]], limit: int = 3) -> list[Hit]:
        result, seen = [], set()
        for number, score, coverage in ranked:
            chunk = self.chunks[number]
            if chunk.url in seen:  # one fragment per section keeps the links distinct
                continue
            seen.add(chunk.url)
            result.append(Hit(chunk, round(score, 4), round(coverage, 4)))
            if len(result) == limit:
                break
        return result

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        return self.hits(self.rank(query), limit)

    def answerable(self, query: str, hits: list[Hit]) -> bool:
        """Refusal gate (FR-H3-03): a term unknown to the whole corpus or a weak best match."""
        return bool(hits) and self.known(query) and hits[0].coverage >= MIN_COVERAGE


@lru_cache(maxsize=1)
def default_index() -> PlaybookIndex:
    """The bundled index is immutable: tokenise it once per process."""
    return PlaybookIndex.load()
