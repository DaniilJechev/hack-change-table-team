"""Live run 12.11, 12:11: «кто за что отвечает?» got the access/person clarify (RT-03, FR-H2-03)."""

import pytest

from buddy.executors.core import TEAM_MENU, template
from buddy.llm.client import FakeLLM
from buddy.router.rules import MENU_CHOICES, TEAM_CHOICES, decide
from buddy.router.service import Router


@pytest.mark.parametrize(
    "text", ["кто за что отвечает?", "напиши структуру команды", "Состав команды", "кто есть кто"]
)
def test_fr_h2_03_q1_team_list_without_area(text):
    assert decide(text).case == "team_q1" and decide(text).params == {}


def test_fr_h2_03_q1_area_question_keeps_its_area():
    assert decide("кто отвечает за бэкенд?").params == {"area": "кто отвечает за бэкенд?"}


@pytest.mark.parametrize("text", ["Нужен доступ к Олегу", "а это кто такой?", "права на репо"])
def test_rt_03_access_or_person_pair_only_when_mentioned(text):
    assert decide(text).params == {"options": TEAM_CHOICES}


@pytest.mark.parametrize("text", ["Ну и как?", "абракадабра", ""])
def test_rt_03_other_unclear_text_gets_team_menu(text):
    assert decide(text).case == "clarify" and decide(text).params == {"options": MENU_CHOICES}


async def test_rt_03_clarify_options_are_chosen_by_code_not_by_llm():
    llm = FakeLLM('{"case": "clarify", "params": {"options": "Доступ к инструменту | Кто это"}}')
    decision = await Router(llm).route("ну и что там")
    assert decision.params == {"options": MENU_CHOICES}
    person = await Router(FakeLLM('{"case": "clarify"}')).route("кому из коллег написать")
    assert person.params == {"options": TEAM_CHOICES}


def test_rt_03_team_menu_clarify_has_question_type_buttons():
    menu = template("clarify", {"options": MENU_CHOICES})
    assert menu.result.startswith("Не понял вопрос.") and "/help" in menu.result
    assert [action.data for action in menu.actions] == ["case:" + case for case, _ in TEAM_MENU]
    pair = template("clarify", {"options": TEAM_CHOICES})
    assert pair.result.startswith("Уточните: вас интересует доступ") and pair.actions == []


@pytest.mark.parametrize(
    "text",
    ["дай доступ к GitHub — Закрытая конфигурация production", "дай доступ к Miro", "нужен доступ к ORD-118"],
)
async def test_rt_03_access_request_is_not_refused_when_llm_says_out_of_scope(text):
    """B-10 (live run 12:37): Haiku took «дай доступ» for a write request and refused."""
    decision = await Router(FakeLLM('{"case": "out_of_scope"}')).route(text)
    assert decision.case in {"access_status", "access_task_analysis"}


async def test_rt_03_real_refusals_stay_out_of_scope():
    llm = FakeLLM('{"case": "out_of_scope"}')
    assert (await Router(llm).route("выдай права на репозиторий")).case == "out_of_scope"
    assert (await Router(llm).route("напиши рецепт борща")).case == "out_of_scope"
