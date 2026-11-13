"""Live run 12.11, round 3: Telegram markup (FR-UI-02), whole lists in H3 (B-01), B-07.

FR-UI-01 (expandable quotes) is removed by `openspec/changes/telegram-citations`: nothing is folded."""

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from buddy.bot.stage_two import rich
from buddy.demo.controller import DemoController
from buddy.executors.core import format_response
from buddy.llm.client import FakeLLM
from buddy.rag.index import PlaybookIndex
from buddy.rag.qa import PROMPT, PlaybookService
from buddy.rag.search import PlaybookSearch
from buddy.render import plain, to_html
from buddy.schemas import ExecutorResponse

LINKS = [f"https://example.invalid/{n}" for n in range(1, 6)]


def test_fr_ui_02_secondary_blocks_follow_as_plain_text():
    html = to_html("Кто за что отвечает — 4 человек.", ["Олег (Тимлид)\nПётр"], LINKS[:2], "Демо.\n")
    head = (
        f'Демо.\nКто за что отвечает — 4 человек. <a href="{LINKS[0]}">[1]</a> <a href="{LINKS[1]}">[2]</a>\n'
    )
    assert html == head + "Олег (Тимлид)\nПётр"  # FR-UI-03


def test_fr_ui_02_long_answer_is_not_folded():
    """Telegram 13.11, 11:20: an agent answer about access was cut after its 6th line into a quote."""
    text = "Вот как сейчас с доступами:\n\nЕсть (4):\n" + "\n".join(f"— пункт {n}" for n in range(1, 12))
    text += "\n\nНет:\n— Miro, доска планирования: доступа нет"
    html = to_html(text, [], [])
    assert html == text and "blockquote" not in html


def test_fr_ui_02_text_is_escaped_and_only_code_adds_tags():
    html = to_html("PR <script>&co</script> **важно**", ["<b>не тег</b>"], ["https://x.invalid/?a=1&b=2"])
    assert "&lt;script&gt;&amp;co&lt;/script&gt;" in html and "<b>важно</b>" in html
    assert "&lt;b&gt;не тег&lt;/b&gt;" in html and "https://x.invalid/?a=1&amp;b=2" in html


def test_b_07_markdown_emphasis_is_not_shown_as_asterisks():
    assert plain("**Миссия:** помогать. __Ценности:__ пять.") == "Миссия: помогать. Ценности: пять."
    response = ExecutorResponse(result="**Миссия**", folded=["список"], links=LINKS[:1])
    assert format_response(response) == "Миссия [1]\nсписок\n[1] " + LINKS[0]
    assert "Markdown" in PROMPT


async def test_fr_ui_02_plain_text_when_telegram_rejects_markup():
    method = AsyncMock(side_effect=[TelegramBadRequest(SendMessage(chat_id=1, text="x"), "bad"), "sent"])
    response = ExecutorResponse(result="**Ответ**", folded=["детали"], links=LINKS[:1])
    assert await rich(method, response, "Демо.\n", disable_web_page_preview=True) == "sent"
    first, second = method.await_args_list
    assert first.kwargs["parse_mode"] == "HTML" and first.kwargs["text"].endswith("</a>\nдетали")
    assert second.kwargs == {
        "text": "Демо.\nОтвет [1]\nдетали\n[1] " + LINKS[0],
        "parse_mode": None,
        "disable_web_page_preview": True,
    }


@pytest.fixture(scope="module")
def index() -> PlaybookIndex:
    return PlaybookIndex.load()


def context():
    controller = DemoController(True)
    controller.command(1)
    return controller.context(1)


async def test_fr_h3_08_whole_list_of_values_is_given_and_folded(index):
    """B-01, round 3: only 2 of 5 values reached the model; a list found in part is given whole."""
    llm = FakeLLM(
        "Миссия Авито — помочь каждому улучшить собственную жизнь. Ценности: всё начинается "
        "с потребностей пользователей, решай быстро и аргументированно, упрощай, "
        "вместе мы можем больше, главное — результат."
    )
    response = await PlaybookService(PlaybookSearch(index), llm).answer(
        "расскажи про ценности и миссию авито", context()
    )
    for number in range(1, 6):
        assert f"Ценность № {number}" in llm.calls[0]
    values = [link for link in response.links if "mission-and-values.md#ценность--" in link]
    assert len(values) == 5 and any(link.endswith("mission-and-values.md#миссия") for link in response.links)
    assert response.folded[0].startswith("В какие ценности мы верим?\n1. Ценность № 1")
    assert len(response.folded[0].splitlines()) == 6
