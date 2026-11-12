"""Live run fixes for H3 (B-01): compound questions and answers without meta phrases."""

import pytest

from buddy.demo.controller import DemoController
from buddy.llm.client import FakeLLM
from buddy.rag.index import PlaybookIndex
from buddy.rag.qa import DETAILS_PROMPT, PROMPT, PlaybookService, topics, unmeta
from buddy.rag.search import PlaybookSearch

VALUE = "mission-and-values.md#ценность--"
ANSWER = (
    "Миссия Авито — помочь каждому улучшить собственную жизнь и жизнь людей вокруг. "
    "Среди ценностей: всё начинается с потребностей пользователей, упрощай, главное — результат."
)


@pytest.fixture(scope="module")
def index() -> PlaybookIndex:
    return PlaybookIndex.load()


def context():
    controller = DemoController(True)
    controller.command(1)
    return controller.context(1)


async def test_fr_h3_08_no_more_than_four_fragments_without_duplicates(index):
    response = await PlaybookService(PlaybookSearch(index)).answer(
        "Расскажи про ценности и миссию и юниты и кластеры и релизы", context()
    )
    assert 1 <= len(response.links) <= 4 and len(set(response.links)) == len(response.links)
    assert len(response.entities["chunks"]) == len(response.links)


def test_fr_h3_08_only_questions_joining_topics_are_split():
    assert topics("расскажи про ценности и миссию авито") == ["расскажи про ценности", "миссию авито"]
    assert topics("Что такое Zero Bug Policy?") == []
    assert topics("Что и как?") == []  # a part without a searchable word is not a topic


async def test_fr_h3_08_single_topic_question_keeps_its_fragments(index):
    response = await PlaybookService(PlaybookSearch(index)).answer("Как часто релизы?", context())
    assert "processes-and-standards.md#релизы" in response.links[0] and len(response.links) <= 3
    assert response.folded == []  # one section: no list to expand


def test_fr_h3_09_prompts_forbid_meta_phrases_and_name_the_gap():
    for prompt in (PROMPT, DETAILS_PROMPT):
        assert "«фрагменты», «материалы», «контекст»" in prompt
        assert "в playbook не нашёл" in prompt


async def test_fr_h3_09_meta_sentence_is_removed_from_the_answer(index):
    llm = FakeLLM(ANSWER + " Фрагменты не раскрывают ценности компании подробнее.")
    response = await PlaybookService(PlaybookSearch(index), llm).answer(
        "расскажи про ценности и миссию авито", context()
    )
    assert "рагмент" not in response.result and response.result.startswith("Миссия Авито")
    assert unmeta("Фрагменты ничего не говорят. В материалах нет ответа.") == ""


async def test_fr_h3_09_uncovered_part_is_named_and_found_part_is_answered(index):
    llm = FakeLLM(
        "Ценности Авито: всё начинается с потребностей пользователей, упрощай, главное — результат. "
        "О VPN в playbook не нашёл."
    )
    response = await PlaybookService(PlaybookSearch(index), llm).answer(
        "Расскажи про ценности и VPN", context()
    )
    assert "О VPN в playbook не нашёл." in response.result
    assert response.links and all(VALUE in link or "ценности" in link for link in response.links)
