"""Live run 12.11, round 3: folded blocks in Telegram (FR-UI-01/02), whole lists in H3 (B-01), B-07."""

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
from buddy.render import plain, split_long, to_html
from buddy.schemas import ExecutorResponse

LINKS = [f"https://example.invalid/{n}" for n in range(1, 6)]


def test_fr_ui_01_folded_blocks_are_expandable_quotes():
    html = to_html("Кто за что отвечает — 4 человек.", ["Олег (Тимлид)\nПётр"], LINKS[:2], "Демо.\n")
    head = "Демо.\nКто за что отвечает — 4 человек.\n"
    assert html.startswith(head + "<blockquote expandable>Олег (Тимлид)\nПётр")
    assert html.endswith("Источники:\n" + "\n".join(LINKS[:2]))  # 1–3 links stay visible


def test_fr_ui_01_long_source_list_is_folded():
    html = to_html("Ответ.", [], LINKS)
    assert html == "Ответ.\nИсточники:\n<blockquote expandable>" + "\n".join(LINKS) + "</blockquote>"


def test_fr_ui_01_long_text_without_folded_blocks_folds_its_tail_between_lines():
    text = "\n".join(f"{n}. пункт" for n in range(1, 15))
    head, tail = split_long(text)
    assert head.splitlines()[-1] == "6. пункт" and tail.startswith("7. пункт")
    assert split_long("одно длинное предложение " * 60) == ("одно длинное предложение " * 60, "")
    assert split_long("коротко") == ("коротко", "")


def test_fr_ui_02_text_is_escaped_and_only_code_adds_tags():
    html = to_html("PR <script>&co</script> **важно**", ["<b>не тег</b>"], ["https://x.invalid/?a=1&b=2"])
    assert "&lt;script&gt;&amp;co&lt;/script&gt;" in html and "<b>важно</b>" in html
    assert "&lt;b&gt;не тег&lt;/b&gt;" in html and "https://x.invalid/?a=1&amp;b=2" in html


def test_b_07_markdown_emphasis_is_not_shown_as_asterisks():
    assert plain("**Миссия:** помогать. __Ценности:__ пять.") == "Миссия: помогать. Ценности: пять."
    response = ExecutorResponse(result="**Миссия**", folded=["список"], links=LINKS[:1])
    assert format_response(response) == "Миссия\nсписок\nИсточники:\n" + LINKS[0]
    assert "Markdown" in PROMPT


async def test_fr_ui_02_plain_text_when_telegram_rejects_markup():
    method = AsyncMock(side_effect=[TelegramBadRequest(SendMessage(chat_id=1, text="x"), "bad"), "sent"])
    response = ExecutorResponse(result="**Ответ**", folded=["детали"], links=LINKS[:1])
    assert await rich(method, response, "Демо.\n", disable_web_page_preview=True) == "sent"
    first, second = method.await_args_list
    assert first.kwargs["parse_mode"] == "HTML" and "<blockquote expandable>детали" in first.kwargs["text"]
    assert second.kwargs == {
        "text": "Демо.\nОтвет\nдетали\nИсточники:\n" + LINKS[0],
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
