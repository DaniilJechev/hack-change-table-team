"""FR-H3-07: playbook follow-ups — history-aware query, «подробнее» by chunk_id, groundedness check."""

from unittest.mock import patch

import pytest

from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry, format_response
from buddy.llm.client import FakeLLM
from buddy.rag.index import PlaybookIndex
from buddy.rag.qa import GROUNDED_SHARE, LONG_EXCERPT, NOTE, PlaybookService, grounded, stems
from buddy.rag.search import PlaybookSearch
from buddy.router.service import Router
from buddy.service import AgentService

ZERO_BUG = "development-principles.md"
UNRELATED = "Солнечная погода радует туристов, поэтому пляжи вечером переполнены отдыхающими горожанами."


@pytest.fixture(scope="module")
def index() -> PlaybookIndex:
    return PlaybookIndex.load()


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller.runtime(1), controller.context(1)


def demo_service() -> tuple[AgentService, object]:
    service = AgentService(Router(), demo_mode=True, settings=Settings(_env_file=None, agent_engine="graph"))
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_fr_h3_07_answer_stores_chunk_ids_not_shown(index):
    runtime, context = demo()
    service = PlaybookService(PlaybookSearch(index), team=Registry().team)
    response = await service.answer("Что такое Zero Bug Policy?", context, runtime)
    chunks = response.entities["chunks"]
    assert chunks and all(item in index.by_id for item in chunks)
    assert [index.chunks[index.by_id[item]].url for item in chunks] == response.links
    assert not any(item in format_response(response) for item in chunks)
    refused = await service.answer("Какой пароль от Wi-Fi в офисе?", context, runtime)
    assert refused.entities == {}


async def test_fr_h3_07_followup_with_history():
    service, context = demo_service()
    seen: list[str] = []
    search = service.executors.playbook.search
    original = search.search

    async def spy(query, limit=3):
        seen.append(query)
        return await original(query, limit)

    with patch.object(search, "search", spy):
        first_decision, first = await service.handle("Что такое Zero Bug Policy?", context)
        decision, response = await service.handle("а кто за неё отвечает?", context)
    assert first_decision.case == "playbook_qa" and ZERO_BUG in first.links[0]
    assert decision.case == "playbook_qa"
    assert "Zero Bug Policy" in seen[-1]
    assert any(ZERO_BUG in link for link in response.links)


async def test_fr_h3_07_details_without_new_search():
    service, context = demo_service()
    _, first = await service.handle("Что такое Zero Bug Policy?", context)
    search = service.executors.playbook.search

    async def forbidden(*args, **kwargs):
        raise AssertionError("«подробнее» must not search the index")

    with patch.object(search, "search", forbidden):
        decision, response = await service.handle("подробнее", context)
    assert decision.case == "playbook_qa"
    assert decision.params["chunks"] == ",".join(first.entities["chunks"])
    assert response.links == first.links and response.entities == first.entities
    assert response.result.startswith("Нашёл в playbook такие разделы:") and response.result.endswith(NOTE)
    assert len(response.result) >= len(first.result)  # excerpts are never shorter than the first answer
    assert [action.label for action in response.actions] == ["Помогло", "Не помогло"]
    assert response.actions[0].data.startswith("rate:bot:b-")


async def test_fr_h3_07_details_with_llm_uses_only_stored_fragments(index):
    runtime, context = demo()
    first = await PlaybookService(PlaybookSearch(index)).answer("Что такое Zero Bug Policy?", context)
    chunks = first.entities["chunks"]
    text = index.chunks[index.by_id[chunks[0]]].text
    llm = FakeLLM(" ".join(text.split()[:60]))
    service = PlaybookService(PlaybookSearch(index), llm, Registry().team)
    response = await service.details(chunks, "Что такое Zero Bug Policy?", context, runtime)
    assert "до 8 предложений" in llm.calls[0] and "https://" not in llm.calls[0]
    assert response.result.startswith(text.split()[0]) and response.links == first.links
    assert response.actions[0].data.startswith("rate:bot:b-")
    refused = await PlaybookService(PlaybookSearch(index), FakeLLM("NO_ANSWER")).details(
        chunks, "Что такое Zero Bug Policy?", context
    )
    assert refused.result.startswith("В playbook этого нет")


async def test_fr_h3_07_details_unknown_ids_fall_back_to_search(index):
    context = demo()[1]
    service = PlaybookService(PlaybookSearch(index))
    for ids in ([], ["no-such-chunk"]):
        response = await service.details(ids, "Что такое Zero Bug Policy?", context)
        assert ZERO_BUG in response.links[0]


async def test_fr_h3_07_details_long_excerpts(index):
    context = demo()[1]
    long = [chunk.id for chunk in index.chunks if len(chunk.text) > LONG_EXCERPT][:1]
    response = await PlaybookService(PlaybookSearch(index)).details(long, "подробнее", context)
    line = response.result.splitlines()[1]
    assert 320 < len(line) and len(line.split("): ", 1)[1]) <= LONG_EXCERPT + 1


async def test_fr_h3_07_ungrounded_answer_shows_fragments(index):
    context = demo()[1]
    service = PlaybookService(PlaybookSearch(index), FakeLLM(UNRELATED))
    response = await service.answer("Что такое Zero Bug Policy?", context)
    assert response.result.startswith("Нашёл в playbook такие разделы:")
    assert "туристов" not in response.result and ZERO_BUG in response.links[0]
    details = await service.details(response.entities["chunks"], "Что такое Zero Bug Policy?", context)
    assert details.result.startswith("Нашёл в playbook такие разделы:") and "туристов" not in details.result


def test_fr_h3_07_groundedness_rule(index):
    chunk = next(chunk for chunk in index.chunks if "Zero Bug" in chunk.heading)
    paraphrase = "Zero Bug Policy — это правило сразу решать судьбу каждого бага и не копить их в бэклоге."
    assert grounded(paraphrase, [chunk])
    assert not grounded(UNRELATED, [chunk])
    assert not grounded("Это так.", [chunk])  # no content words at all
    assert not grounded("Policy туристы пляжи горожане вечером", [chunk])  # one shared stem only
    assert 0 < GROUNDED_SHARE < 1 and stems("Ёжики ЕЖИКОВ и") == {"ежики", "ежико"}


async def test_fr_h3_07_followup_out_of_corpus():
    service, context = demo_service()
    await service.handle("Что такое Zero Bug Policy?", context)
    decision, response = await service.handle("а пароль от Wi-Fi?", context)
    assert decision.case == "playbook_qa"
    assert response.result.startswith("В playbook этого нет") and response.links == []
    assert [(action.label, action.data) for action in response.actions] == [
        ("Спросить наставника", "esc:mentor")
    ]
