"""PL-01/02: regressions from the live Telegram values dialog, no network."""

import pytest
from test_agent_orchestrator import call, setup

from buddy.agent import Agent
from buddy.rag.index import default_index, search_query, tokens
from buddy.rag.qa import NOTE
from buddy.rag.search import PlaybookSearch


@pytest.mark.parametrize("question", [
    "Какие ценности у Авито?",
    "поищи что-то про ценности в авито",
    "Пожалуйста, найди информацию о ценностях Авито",
    "Найдите материалы про ценности Авито",
])
async def test_pl_01_values_search_accepts_request_prefix(question):
    found = await PlaybookSearch(default_index()).search(question)
    assert found.answerable
    assert found.hits[0].chunk.file == "mission-and-values.md"


@pytest.mark.parametrize("question", [
    "Найди информацию о квазизяблике",
    "поищи что-то про пароль от Wi-Fi в офисе",
    "поищи что-то про ценности и квазизяблика в Авито",
])
async def test_pl_01_unknown_topic_is_not_removed(question):
    found = await PlaybookSearch(default_index()).search(question)
    assert not found.answerable


def test_pl_01_only_prefix_is_removed():
    assert search_query("Найди информацию о ценностях и неизвестном") == "ценностях и неизвестном"
    assert search_query("Почему люди ищут ценности?") == "Почему люди ищут ценности?"
    assert search_query("поищи что-то про") == ""


def test_pl_01_abstract_noun_case_forms_share_stem():
    assert tokens("ценность") == tokens("ценности") == tokens("ценностях") == tokens("ценностями")
    assert tokens("возможность") == tokens("возможности") == tokens("возможностях")


async def test_pl_02_false_refusal_history_requires_fresh_executor():
    _, llm, registry, runtime, context = setup(
        {"content": "В playbook про ценности ничего нет. Спросите наставника."},
        {"content": "Поискал, там ничего нет."},
    )
    agent = Agent(llm, registry)
    history = [
        {"role": "user", "content": "Какие ценности у Авито?"},
        {"role": "assistant", "content": "В playbook ничего не нашлось."},
    ]
    for question in ["Какие ценности у Авито?", "поищи что-то про ценности в авито"]:
        decision, response = await agent.answer(question, context, runtime, history)
        assert decision.case == "playbook_qa"
        assert NOTE in response.result
        combined = response.result + "\n" + "\n".join(response.folded)
        for word in ["пользовател", "быстро", "упрощ", "вместе", "результат"]:
            assert word in combined.casefold()
        assert len(response.links) >= 5
        assert all("mission-and-values.md" in url for url in response.links)
    assert all(choice == ("required", ["playbook_qa"]) for choice in llm.choices)


async def test_pl_02_outer_agent_cannot_deny_found_values():
    ask, llm, *_ = setup(
        {"tool_calls": [call("playbook_qa", {"query": "ценности Авито"})]},
        {"content": "В playbook про ценности ничего нет."},
    )
    response = (await ask("Какие ценности у Авито?"))["response"]
    assert "В playbook про ценности ничего нет" not in response.result
    assert len(response.links) >= 5 and NOTE in response.result


async def test_pl_02_unknown_topic_keeps_real_refusal():
    ask, *_ = setup({"content": "Не знаю."})
    response = (await ask("Найди в playbook сведения о квазизяблике"))["response"]
    assert "В playbook этого нет" in response.result
    assert not response.links
    assert any(action.label == "Спросить наставника" for action in response.actions)


async def test_pl_02_unrelated_tool_does_not_replace_search():
    ask, *_ = setup({"tool_calls": [call("team_q3")]})
    state = await ask("Какие ценности у Авито?")
    assert state["decision"].case == "playbook_qa"
    assert any("mission-and-values.md" in url for url in state["response"].links)
