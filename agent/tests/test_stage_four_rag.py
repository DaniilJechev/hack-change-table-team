"""H3 (stage 4): playbook index, three search modes, answers with links, refusal, no-LLM fallback."""

import json
import sys
from pathlib import Path

import pytest

from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry, format_response
from buddy.llm.client import FakeLLM
from buddy.rag import ingest
from buddy.rag.chunks import MAX_CHARS, anchor, split
from buddy.rag.index import INDEX, PlaybookIndex, tokens
from buddy.rag.qa import NOTE, PlaybookService
from buddy.rag.search import PlaybookSearch, create_search
from buddy.rag.vectors import DIMENSIONS, E5Embedder, PgVectorStore, VectorUnavailable, literal

ROOT = Path(__file__).resolve().parents[2]
COMMIT = ingest.COMMIT
PAGE = """# Учебная страница

- [Раздел](#раздел)
- [Другой](#другой)

## Раздел про Kafka: зачем?
Kafka нужна как шина событий между учебными сервисами. Это синтетический текст для теста, он не из playbook.

### Вложенный
Короткий.

## Раздел про Kafka: зачем?
Повтор заголовка получает номер, чтобы якорь остался уникальным на странице и ссылка вела точно в раздел.

```
# это код, а не заголовок
```
"""


@pytest.fixture(scope="module")
def index() -> PlaybookIndex:
    return PlaybookIndex.load()


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller.runtime(1), controller.context(1)


class Vectors:
    """Deterministic fake embedder: one axis per marker word."""

    AXES = ("zero", "юнит", "релиз", "wi")

    def __init__(self):
        self.queries = 0

    def _vector(self, text: str) -> list[float]:
        return [1.0 if axis in text.casefold() else 0.01 for axis in self.AXES]

    def query(self, text: str) -> list[float]:
        self.queries += 1
        return self._vector(text)

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


class Memory:
    """Fake of the pgvector table: cosine search over stored rows."""

    def __init__(self):
        self.rows: dict[str, list[float]] = {}
        self.fail = False

    def replace(self, commit, chunks, vectors):
        self.rows = {chunk.id: vector for chunk, vector in zip(chunks, vectors, strict=True)}
        return len(self.rows)

    def search(self, commit, vector, limit):
        if self.fail:
            raise VectorUnavailable("database")

        def cosine(left, right):
            norm = sum(x * x for x in left) ** 0.5 * sum(x * x for x in right) ** 0.5
            return sum(x * y for x, y in zip(left, right, strict=True)) / norm

        scored = sorted(((cosine(vector, row), key) for key, row in self.rows.items()), reverse=True)
        return [(key, score) for score, key in scored[:limit]]


def test_fr_h3_01_chunks_by_headings_with_file_heading_and_anchor_url():
    chunks = split("page.md", PAGE, "abc123")
    assert [chunk.heading for chunk in chunks] == [
        "Учебная страница › Раздел про Kafka: зачем?",
        "Учебная страница › Раздел про Kafka: зачем?",
    ]  # the table of contents, the tiny section and the fenced "heading" are skipped
    assert (
        chunks[0].url == "https://github.com/avito-tech/playbook/blob/abc123/page.md#раздел-про-kafka-зачем"
    )
    assert chunks[1].url.endswith("#раздел-про-kafka-зачем-1")
    assert all(chunk.file == "page.md" and chunk.text and len(chunk.id) == 16 for chunk in chunks)
    long = split("long.md", "# Длинный\n" + ("Предложение про процесс. " * 200), "abc123")
    assert len(long) > 1 and all(len(chunk.text) <= MAX_CHARS for chunk in long)
    assert len({chunk.id for chunk in long}) == len(long)
    assert anchor("Ценность № 1: всё начинается!", {}) == "ценность--1-всё-начинается"
    solid = split("solid.md", "# Сплошной\n" + "ы" * 3000, "abc123")
    assert all(len(chunk.text) <= MAX_CHARS for chunk in solid)


def test_fr_h3_01_committed_index_is_reproducible_from_the_snapshot(tmp_path, index):
    built = ingest.build()
    assert built == json.loads(INDEX.read_text(encoding="utf-8"))  # make ingest gives the same file
    assert built["commit"] == COMMIT and built["files"] == 28 and len(built["chunks"]) == len(index.chunks)
    assert all(
        chunk["file"]
        and chunk["heading"]
        and chunk["text"]
        and chunk["url"].startswith(f"https://github.com/avito-tech/playbook/blob/{COMMIT}/{chunk['file']}#")
        for chunk in built["chunks"]
    )
    with pytest.raises(ValueError, match="empty"):
        ingest.build(tmp_path)


def test_fr_h3_01_repeated_ingest_keeps_fragment_count_without_duplicates(tmp_path, capsys):
    store, output = Memory(), tmp_path / "index.json"
    settings = Settings(rag_search_mode="rrf", rag_database_url="postgresql://synthetic/db")
    for _ in range(2):
        assert ingest.main(["--output", str(output)], settings=settings, embedder=Vectors(), store=store) == 0
    count = len(json.loads(output.read_text(encoding="utf-8"))["chunks"])
    assert len(store.rows) == count and f"Stored {count} embeddings" in capsys.readouterr().out
    assert ingest.main(["--output", str(output)], settings=Settings()) == 0  # bm25: no embeddings at all
    assert "Embeddings skipped" in capsys.readouterr().out
    assert ingest.main(["--output", str(output), "--embed", "yes"], settings=Settings()) == 2
    assert "RAG_DATABASE_URL" in capsys.readouterr().out
    broken = Memory()
    broken.replace = lambda *args: (_ for _ in ()).throw(VectorUnavailable("database"))
    assert ingest.main(["--output", str(output)], settings=settings, embedder=Vectors(), store=broken) == 2


def test_fr_h3_01_pgvector_store_replaces_rows_in_one_transaction():
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split())[:40], params))

        def executemany(self, sql, rows):
            executed.append(("many", len(rows)))

        def fetchone(self):
            return (2,)

        def fetchall(self):
            return [("id-1", 0.91), ("id-2", 0.5)]

    class Connection:
        closed = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

        def close(self):
            Connection.closed += 1

    store = PgVectorStore("postgresql://synthetic/db", connect=lambda dsn: Connection())
    chunks = split("page.md", PAGE, "abc123")
    assert store.replace("abc123", chunks, [[0.5] * DIMENSIONS] * 2) == 2
    assert [entry[0][:12] for entry in executed] == ["DELETE FROM ", "many"]
    assert store.count("abc123") == 2 and store.search("abc123", [0.1] * DIMENSIONS, 5)[0] == ("id-1", 0.91)
    assert Connection.closed == 3 and literal([0.5, 1]) == "[0.500000,1.000000]"
    with pytest.raises(ValueError):
        store.replace("abc123", chunks, [[0.5] * 3] * 2)
    down = PgVectorStore("x", connect=lambda dsn: (_ for _ in ()).throw(OSError("refused")))
    with pytest.raises(VectorUnavailable):
        down.count("abc123")

    class Failing(Connection):
        def cursor(self):
            raise RuntimeError("syntax")

    with pytest.raises(VectorUnavailable):
        PgVectorStore("x", connect=lambda dsn: Failing()).count("abc123")


async def test_fr_h3_02_zero_bug_policy_answer_links_file_and_section(index):
    runtime, context = demo()
    llm = FakeLLM("Zero Bug Policy — это правило сразу решать судьбу каждого бага и не копить их в бэклоге.")
    service = PlaybookService(PlaybookSearch(index), llm, Registry().team)
    response = await service.answer("Что такое Zero Bug Policy?", context, runtime)
    assert response.result.startswith("Zero Bug Policy — это правило") and response.result.endswith(NOTE)
    assert response.links[0] == (
        f"https://github.com/avito-tech/playbook/blob/{COMMIT}/development-principles.md"
        "#мы-работаем-по-zero-bug-policy"
    )
    assert "https" not in response.result and "\n[1] " + response.links[0] in format_response(response)
    assert "[1]" in llm.calls[0] and "https://" not in llm.calls[0]  # the LLM never sees links
    assert response.actions[0].data.startswith("rate:bot:b-")  # the rating belongs to a stored answer


async def test_fr_h3_02_structure_question_links_structure_file(index):
    response = await PlaybookService(PlaybookSearch(index)).answer("Что такое юнит и кластер?", demo()[1])
    assert "structure.md" in response.links[0] and "Структура" in response.result


async def test_fr_h3_03_out_of_corpus_question_is_refused_with_mentor_button(index):
    runtime, context = demo()
    registry = Registry()
    response = await registry.execute("playbook_qa", {}, context, runtime, "Какой пароль от Wi-Fi в офисе?")
    assert response.result.startswith("В playbook этого нет") and response.links == []
    assert [(action.label, action.data) for action in response.actions] == [
        ("Спросить наставника", "esc:mentor")
    ]
    reply, notes = await registry.team.escalate("mentor", context, runtime)
    assert "Передал вопрос" in reply.result and notes[0][0] == "demo-anna"
    assert "Бот проверил playbook — ответа нет." in notes[0][1].result
    assert "Задайте вопрос" in (await registry.playbook.answer("  ", context, runtime)).result
    assert index.search("", 3) == [] and not index.answerable("абракадабрус", [])


async def test_fr_h3_03_llm_can_refuse_when_fragments_do_not_answer(index):
    service = PlaybookService(PlaybookSearch(index), FakeLLM("NO_ANSWER"))
    assert (await service.answer("Что такое TDR?", demo()[1])).result.startswith("В playbook этого нет")


@pytest.mark.parametrize(
    "question", ["Что такое TDR?", "Что такое соседское соглашение?", "Какая миссия у Авито?"]
)
async def test_fr_h3_04_every_answer_has_the_actuality_note(index, question):
    response = await PlaybookService(PlaybookSearch(index)).answer(question, demo()[1])
    assert response.result.endswith("ℹ️ Playbook — публичный документ Авито; он может быть устаревшим.")


@pytest.mark.parametrize("raw", [None, "", "Смотрите https://evil.example/x — там всё написано подробно."])
async def test_fr_h3_05_without_usable_llm_three_fragments_with_links(index, raw):
    llm = FakeLLM(raw) if raw is not None else FakeLLM()
    response = await PlaybookService(PlaybookSearch(index), llm).answer("Как часто релизы?", demo()[1])
    assert response.result.startswith("Нашёл в playbook такие разделы:\n1. Процессы и стандарты › Релизы")
    assert "processes-and-standards.md#релизы" in response.links[0] and 1 <= len(response.links) <= 3
    assert "evil" not in format_response(response)


async def test_fr_h3_06_bm25_mode_never_loads_the_model(index):
    search = create_search(Settings(), index)
    assert search.mode == "bm25" and search.embedder is None and search.store is None
    found = await search.search("Что такое Zero Bug Policy?")
    assert found.answerable and found.mode == "bm25" and not found.degraded
    assert "torch" not in sys.modules and "sentence_transformers" not in sys.modules
    without_database = create_search(Settings(rag_search_mode="rrf"), index)
    assert without_database.mode == "bm25"  # vector modes need RAG_DATABASE_URL
    configured = create_search(
        Settings(rag_search_mode="vector", rag_database_url="postgresql://synthetic/db"), index
    )
    assert configured.mode == "vector" and not configured.embedder.loaded  # lazy: nothing loaded yet
    assert "torch" not in sys.modules


async def test_fr_h3_06_vector_and_rrf_modes_with_fake_embeddings(index):
    embedder, store = Vectors(), Memory()
    store.replace(
        index.commit, index.chunks, embedder.passages([f"{c.heading}\n{c.text}" for c in index.chunks])
    )
    vector = PlaybookSearch(index, "vector", embedder, store, min_similarity=0.5)
    found = await vector.search("Что такое Zero Bug Policy?")
    assert found.mode == "vector" and found.answerable and "zero" in found.hits[0].chunk.heading.casefold()
    rrf = PlaybookSearch(index, "rrf", embedder, store, min_similarity=0.5)
    fused = await rrf.search("Что такое Zero Bug Policy?")
    assert fused.mode == "rrf" and fused.answerable
    assert fused.hits[0].chunk.file == "development-principles.md"
    refused = await rrf.search("Какой пароль от Wi-Fi в офисе?")
    assert not refused.answerable  # «пароль» is unknown to the playbook even if a vector is close
    strict = PlaybookSearch(index, "vector", embedder, store, min_similarity=1.01)
    assert not (await strict.search("Расскажи про процессы")).answerable


async def test_fr_h3_06_vector_modes_degrade_to_bm25_when_model_or_database_is_down(index, caplog):
    store = Memory()
    store.fail = True
    search = PlaybookSearch(index, "rrf", Vectors(), store)
    for _ in range(2):
        found = await search.search("Что такое Zero Bug Policy?")
        assert found.degraded and found.mode == "bm25" and found.answerable
    assert caplog.text.count("Vector search unavailable") == 1  # warned once, not on every question
    empty = PlaybookSearch(index, "vector", Vectors(), Memory())
    assert (await empty.search("Что такое TDR?")).degraded  # nothing ingested yet
    assert (await PlaybookSearch(index, "vector").search("Что такое TDR?")).degraded


def test_h3_e5_embedder_is_lazy_cached_and_prefixes_roles():
    loads, seen = [], []

    class Model:
        def encode(self, texts, normalize_embeddings, batch_size):
            seen.extend(texts)
            return [[0.1, 0.2] for _ in texts]

    embedder = E5Embedder("synthetic-model", cache=1, loader=lambda name: loads.append(name) or Model())
    assert not embedder.loaded and loads == []
    assert embedder.query("Что такое TDR?") == [0.1, 0.2] and embedder.loaded
    embedder.query("что такое tdr? ")  # cached: the model is not called again
    embedder.query("Другой вопрос")
    assert embedder.passages(["текст"]) == [[0.1, 0.2]] and loads == ["synthetic-model"]
    assert seen == ["query: Что такое TDR?", "query: Другой вопрос", "passage: текст"]
    assert len(embedder._cache) == 1
    with pytest.raises(VectorUnavailable):
        E5Embedder("x", loader=lambda name: (_ for _ in ()).throw(ImportError("no torch"))).query("q")

    class Broken:
        def encode(self, *args, **kwargs):
            raise RuntimeError("oom")

    with pytest.raises(VectorUnavailable):
        E5Embedder("x", loader=lambda name: Broken()).query("q")


def test_h3_tokens_stem_and_expand_colloquial_synonyms():
    assert tokens("Чем занимается техлид?") == ["занима", "tech", "lead"]
    assert tokens("в и на") == []


async def test_h3_registry_routes_playbook_question_without_runtime(index):
    context = demo()[1]
    response = await Registry().execute("playbook_qa", {"query": "Что такое TDR?"}, context, None, "")
    assert "tech_design_review.md" in response.links[0]
    assert response.actions[0].data == "rate:bot:playbook_qa:1"  # nothing stored without a runtime
