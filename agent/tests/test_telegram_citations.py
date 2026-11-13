"""FR-UI-03: source links are footnotes [n] made by code, clickable in Telegram HTML."""

import json
import re
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from buddy.agent import Agent
from buddy.bot.stage_two import rich
from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.executors.core import format_response
from buddy.graph.build import guarded
from buddy.llm.client import FakeLLM
from buddy.rag.index import PlaybookIndex
from buddy.rag.qa import NOTE, PlaybookService
from buddy.rag.search import PlaybookSearch
from buddy.render import LIMIT, cite, to_html, to_plain
from buddy.router.service import Router
from buddy.schemas import ExecutorResponse
from buddy.service import AgentService

U1, U2, U3 = (f"https://example.invalid/{n}" for n in range(1, 4))


def a(url: str, number: int) -> str:
    return f'<a href="{url}">[{number}]</a>'


def test_fr_ui_03_brief_numbered_lines_get_their_own_footnote():
    brief = "Бриф на 11.11:\n▶ Главное сегодня: ревью\n1. Ревью PR по авторизации\n2. Задача ORD-118"
    assert to_html(brief, [], [U1, U2]) == (
        f"Бриф на 11.11:\n▶ Главное сегодня: ревью\n1. Ревью PR по авторизации {a(U1, 1)}\n"
        f"2. Задача ORD-118 {a(U2, 2)}"
    )


def test_fr_ui_03_same_url_gets_one_number():
    assert cite("1. PR\n2. Комментарий в PR", [], [U1, U1]) == (
        "1. PR [1]\n2. Комментарий в PR [1]",
        [],
        [U1],
    )
    assert cite("Ответ.", [], [U1, U2, U1]) == ("Ответ. [1] [2]", [], [U1, U2])


def test_fr_ui_03_single_paragraph_answer_gets_footnotes_at_its_last_line():
    html = to_html("Наставник — Анна (backend).\nТимлид — Олег.\n", [], [U1, U2])
    assert html == f"Наставник — Анна (backend).\nТимлид — Олег. {a(U1, 1)} {a(U2, 2)}\n"
    assert "Источники" not in html


def test_fr_ui_03_response_without_sources_has_no_links():
    assert to_html("Ответ [3].", [], []) == "Ответ."
    assert format_response(ExecutorResponse(result="Ответ [3].")) == "Ответ."


def test_fr_ui_03_agent_footnotes_are_kept():
    text = "Анна — наставник [1]. PR #45 ждёт ревью [2]."
    assert cite(text, [], [U1, U2]) == (text, [], [U1, U2])
    assert to_html(text, [], [U1, U2]) == f"Анна — наставник {a(U1, 1)}. PR #45 ждёт ревью {a(U2, 2)}."


def test_fr_ui_03_text_and_href_are_escaped():
    html = to_html('PR <script>&"', [], ['https://x.invalid/?a=1&b="2"'])
    assert html == 'PR &lt;script&gt;&amp;" <a href="https://x.invalid/?a=1&amp;b=&quot;2&quot;">[1]</a>'


def test_fr_ui_03_footnotes_inside_folded_block_are_links():
    html = to_html("Последний бриф — ниже.", ["Пункты:\n1. PR\n2. Задача"], [U1, U2])
    quote = f"<blockquote expandable>Пункты:\n1. PR {a(U1, 1)}\n2. Задача {a(U2, 2)}</blockquote>"
    assert html == "Последний бриф — ниже.\n" + quote


def test_fr_ui_03_plain_text_keeps_footnotes_and_a_numbered_list():
    assert to_plain("**Ответ**", ["детали"], [U1, U1, U2]) == f"Ответ [1] [2]\nдетали\n[1] {U1}\n[2] {U2}"


async def test_fr_ui_03_long_answer_fits_the_telegram_limit():
    result = "\n".join(f"Строка {n:03d} " + "x" * 30 for n in range(110))  # ~4.5k characters
    response = ExecutorResponse(result=result, links=[U1, U2, U3])
    html = to_html(response.result, [], response.links)
    assert html.count("<a ") == html.count("</a>") == 3 and len(re.sub(r"<[^>]+>", "", html)) > LIMIT
    method = AsyncMock(side_effect=[TelegramBadRequest(SendMessage(chat_id=1, text="x"), "too long"), "sent"])
    assert await rich(method, response, "Демо.\n") == "sent"
    text = method.await_args_list[1].kwargs["text"]
    assert len(text) <= LIMIT and text.startswith("Демо.\nСтрока 000")
    assert text.endswith(f"\n[1] {U1}\n[2] {U2}\n[3] {U3}")
    assert re.search(r"\nСтрока \d{3} x{30}\n\[1\] ", text)  # cut between lines, never mid-line


def test_fr_ui_03_guard_adds_footnotes_once():
    response = guarded(ExecutorResponse(result="Наставник — Анна.", links=[U1, U1]), "team_q3")
    assert (response.result, response.links) == ("Наставник — Анна. [1]", [U1])
    assert guarded(response, "team_q3") == response


async def test_fr_ui_03_playbook_answer_gets_code_footnotes_not_llm_numbers():
    llm = FakeLLM(
        "Миссия Авито [1] — помочь каждому улучшить собственную жизнь. Ценности [7]: всё начинается "
        "с потребностей пользователей, решай быстро и аргументированно, упрощай, "
        "вместе мы можем больше, главное — результат."
    )
    controller = DemoController(True)
    controller.command(1)
    response = await PlaybookService(PlaybookSearch(PlaybookIndex.load()), llm).answer(
        "расскажи про ценности и миссию авито", controller.context(1)
    )
    answer, note = response.result.split("\n")
    marks = "".join(f" [{n}]" for n in range(1, 8))
    assert answer.startswith("Миссия Авито — помочь") and answer.endswith("результат." + marks)
    html = to_html(response.result, response.folded, response.links)
    assert note == NOTE and html.count("</a>") == len(response.links) == 7


def demo():
    service = AgentService(Router(), demo_mode=True, settings=Settings(_env_file=None, agent_engine="graph"))
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_fr_ui_03_access_and_team_lines_carry_their_own_footnotes():
    service, context = demo()
    _, access = await service.handle("Что с моими доступами?", context)
    lines = access.result.splitlines()
    # numbered as read: visible lines first, then the folded «Доступ есть»; none on the closing note
    assert lines[1].startswith("⬜ Miro") and lines[1].endswith(" [1]") and lines[2].endswith(" [2]")
    assert not re.search(r"\[\d", lines[-1])
    assert access.folded[0].splitlines()[1].endswith("orders-onboarding: доступ есть [3]")
    _, mentor = await service.handle("Кто мой наставник?", context)
    assert mentor.result == (
        "Ваш наставник — Анна. [1]\nТимлид — Олег. [2]\nПервый контакт в неожиданных ситуациях — Олег. [2]"
    )
    assert len(mentor.links) == 2


def test_fr_ui_03_agent_observation_has_no_code_footnotes():
    sources: list[str] = []
    response = ExecutorResponse(result="Наставник — Анна. [1]", folded=["Олег [2]"], links=[U1, U2])
    observation = json.loads(Agent._observe(response, sources))
    assert observation["result"] == "Наставник — Анна.\nОлег" and sources == [U1, U2]
