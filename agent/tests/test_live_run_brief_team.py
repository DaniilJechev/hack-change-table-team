"""Live run 12.11, 12:45: brief follow-ups by item title and after «Изменений нет»; a person's areas."""

from buddy.config import Settings
from buddy.memory import followup
from buddy.memory.store import Turn
from buddy.router.rules import decide
from buddy.router.service import Router
from buddy.service import AgentService

STANDUP = "https://example.invalid/calendar/event/demo-standup"


def demo():
    service = AgentService(Router(), demo_mode=True, settings=Settings(_env_file=None, agent_engine="graph"))
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_fr_h0_14_follow_up_by_item_title():
    """B-11: «что за учебный стендап?» after the brief — the brief item, not a playbook refusal."""
    service, context = demo()
    await service.handle("/brief", context)
    decision, answer = await service.handle("что за учебный стендап?", context)
    assert decision.case == "brief_now" and decision.params["items"]
    assert answer.result.startswith("Из брифа:") and "Учебный стендап" in answer.result
    assert answer.links == [STANDUP]


def test_fr_h0_14_title_match_needs_every_content_word():
    titles = [("i-1", "«Учебный стендап»"), ("i-2", "«Учебное ретро»")]
    assert followup.by_title("что за учебный стендап", titles) == ["i-1"]
    assert followup.by_title("а что с ретро", titles) == ["i-2"]
    assert followup.by_title("что такое zero bug policy", titles) == []
    assert followup.by_title("что за", titles) == []
    brief = Turn(case="brief_now", entities={"brief": ["br-1"]})
    assert followup.resolve("что за учебный стендап?", [brief]) is None  # no titles source: Router


async def test_fr_h0_14_follow_up_after_no_changes_uses_last_brief():
    """B-13: the second /brief says «Изменений нет»; «что там в Jira?» answers from the last brief."""
    service, context = demo()
    _, first = await service.handle("/brief", context)
    await service.brief_delivered(first, context, True)  # the bot marks a sent brief as delivered
    others = ("дай доступ", "кто Тимлид?", "какие ценности у авито?", "кто за что отвечает?", "ну и как?")
    for text in others:
        await service.handle(text, context)  # the first brief leaves the 6-turn window
    _, empty = await service.handle("/brief", context)
    assert empty.result.startswith("Изменений нет.") and empty.entities["brief"]
    decision, jira = await service.handle("что там в Jira?", context)
    assert decision.params.get("brief_id") and jira.result.startswith("Jira в брифе:")
    assert jira.links and all("/jira/" in link for link in jira.links)


def test_rt_03_my_plan_goes_to_the_brief():
    assert decide("что там сейчас по плану нужно сделать?").case == "brief_now"
    assert decide("что мне надо сделать сегодня").case == "brief_now"
    assert decide("какой план на сегодня?").case == "brief_now"
    assert decide("повтори прошлый план").case == "brief_now"
    assert decide("что там в Jira?").params == {"source": "jira"}
    assert decide("что там в playbook про ревью").case == "playbook_qa"


async def test_fr_h2_11_areas_of_a_named_person():
    """B-14: «за что отвечает олег?» — Олег's areas, whatever the case of the name."""
    service, context = demo()
    for text in ("за что отвечает олег?", "за что отвечает Олег?"):
        decision, answer = await service.handle(text, context)
        assert decision.case == "team_q1"
        assert answer.result == "Олег (Тимлид) отвечает за: orders-api, надёжность."
        assert answer.links == ["https://example.invalid/team/demo-orders/people/demo-oleg"]
    _, kafka = await service.handle("кто отвечает за Kafka?", context)
    assert "Пётр" in kafka.result  # an area question still finds the owner by area


async def test_fr_h0_14_no_changes_reminds_the_last_brief():
    """B-15 (live run 13:15): «какой план на сегодня?» after the brief — not just «Изменений нет»."""
    service, context = demo()
    _, first = await service.handle("/brief", context)
    await service.brief_delivered(first, context, True)
    _, again = await service.handle("какой план на сегодня?", context)
    assert again.result.startswith("Изменений нет.")
    assert "Главное по последнему брифу: исправить замечания в PR #45" in again.result
    lines = again.folded[0].splitlines()
    assert lines[0] == "Пункты последнего брифа:" and lines[1].startswith("1. ") and len(lines) == 9
    assert set(first.links) <= set(again.links)
    _, jira = await service.handle("/brief jira", context)
    assert jira.folded == []  # a one-source quick look stays short
