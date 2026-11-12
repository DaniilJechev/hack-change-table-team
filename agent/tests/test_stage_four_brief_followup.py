"""FR-H0-12: follow-ups about the last brief — item by number or PR, chosen and linked by code."""

from unittest.mock import AsyncMock

from buddy.brief.models import BriefItem
from buddy.brief.service import BriefService, pr_numbers
from buddy.config import Settings
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry, format_response
from buddy.router.service import Router
from buddy.service import AgentService

PR45 = "https://example.invalid/github/demo-buddy/orders-onboarding/pull/45#pullrequestreview-9001"


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller.runtime(1), controller.context(1)


async def brief(service: BriefService, runtime, context):
    response = await service.now_brief(context, runtime)
    await service.delivered(response.brief_id, runtime, True)
    return response


async def test_fr_h0_12_brief_entities_keep_shown_items_in_display_order():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    draft = service.drafts[response.brief_id]
    assert response.entities["brief"] == [response.brief_id]
    assert response.entities["brief_items"] == [item.id for item in draft.shown]
    assert response.entities["pr"] == ["45", "47"]
    assert response.entities["task"][0] == "ORD-118"
    assert "br-" not in format_response(response)  # entities are memory-only, never rendered


async def test_fr_h0_12_details_about_item():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    second = response.result.splitlines()[3]  # title, main action, then «1.», «2.»
    answer = await service.item(context, runtime, response.brief_id, item="2")
    assert answer.result == "Пункт 2 из брифа:\n" + second
    assert answer.links == [response.links[1]]
    assert answer.entities == {
        "brief": [response.brief_id],
        "brief_items": [response.entities["brief_items"][1]],
        "task": ["ORD-118"],
    }


async def test_fr_h0_12_details_about_hidden_item_and_change():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    moved = await service.item(context, runtime, response.brief_id, item="5")
    assert "(15:00 → 12:00)" in moved.result
    hidden = await service.item(context, runtime, response.brief_id, item="8")  # under «Ещё 3»
    assert hidden.result.splitlines()[1].startswith("8. ⚪ Календарь: «Учебное ретро» отменена")
    assert hidden.result.endswith("Было: 17:00.")
    due = await service.item(context, runtime, response.brief_id, item="6")
    assert due.result.endswith("Стало: 12.11.") and due.links == [
        "https://example.invalid/jira/browse/ORD-118"
    ]


async def test_fr_h0_12_pr_from_brief():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    answer = await service.item(context, runtime, response.brief_id, pr="45")
    assert answer.result.splitlines()[0] == "PR #45 в брифе:"
    assert answer.result.splitlines()[1].startswith("1. 🔴 Git: PR #45")
    assert answer.links == [PR45] and answer.entities["pr"] == ["45"]
    other = await service.item(context, runtime, response.brief_id, pr="#47")
    assert other.links == ["https://example.invalid/github/demo-buddy/orders-onboarding/pull/47"]


async def test_fr_h0_12_redirect_tokens_are_honoured():
    runtime, context = demo()
    settings = type(
        "S",
        (),
        {"brief_timezone": "Europe/Moscow", "redirect_base_url": "https://b.example/", "brief_time": ""},
    )
    service = BriefService(settings=settings)
    response = await brief(service, runtime, context)
    service.drafts[response.brief_id].tokens = {response.entities["brief_items"][0]: "tok45"}
    answer = await service.item(context, runtime, response.brief_id, pr="45")
    assert answer.links == ["https://b.example/r/tok45"]


async def test_fr_h0_12_other_source():
    runtime, context = demo()
    registry = Registry()
    await registry.execute("brief_now", {"source": "github"}, context, runtime, "Есть что по PR?")
    calendar = await registry.execute(
        "brief_now", {"source": "calendar"}, context, runtime, "а по календарю?"
    )
    assert "Календарь:" in calendar.result and "Git:" not in calendar.result and "Jira" not in calendar.result
    assert all("/calendar/" in link for link in calendar.links)


async def test_fr_h0_12_item_missing_has_no_links():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    missing = await service.item(context, runtime, response.brief_id, item="9")
    assert missing.result == "В последнем брифе нет пункта 9."
    assert missing.links == [] and missing.entities == {} and "https" not in format_response(missing)
    empty = await service.item(context, runtime, response.brief_id)
    assert empty.result == "В последнем брифе нет такого пункта." and empty.links == []


async def test_fr_h0_12_unknown_or_foreign_brief_is_not_found():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    unknown = await service.item(context, runtime, "br-unknown", item="1")
    assert unknown.result == "В последнем брифе нет пункта 1." and unknown.links == []
    other = DemoController(True)
    other.command(2)
    foreign = await service.item(other.context(2), other.runtime(2), response.brief_id, item="1")
    assert foreign.result == "В последнем брифе нет пункта 1." and foreign.links == []
    stored = BriefService()  # a restarted agent reads the brief back from storage for its owner only
    assert (await stored.item(context, runtime, response.brief_id, item="1")).links == [PR45]
    runtime.data.brief = AsyncMock(side_effect=DataError("unavailable"))
    assert (await BriefService().item(context, runtime, response.brief_id, item="1")).links == []


async def test_fr_h0_12_pr_missing_falls_back_to_git_brief():
    runtime, context = demo()
    service = BriefService()
    response = await brief(service, runtime, context)
    answer = await service.item(context, runtime, response.brief_id, pr="99")
    lines = answer.result.splitlines()
    assert lines[0] == "В последнем брифе нет PR #99."
    assert lines[1] == "Изменений по источнику Git нет."  # FR-H0-07: the brief already reported them
    assert not any("/pull/99" in link for link in answer.links)


def test_fr_h0_12_pr_numbers_only_for_git_items():
    jira = BriefItem(
        id="j-1",
        source="jira",
        type="jira_mention",
        title="PR #45 обсуждают",
        url="https://example.invalid/jira/browse/ORD-1",
        priority_class="people",
        created_at="2026-11-11T09:00:00+03:00",
    )
    assert pr_numbers(jira) == []
    git = jira.model_copy(update={"source": "github", "url": "https://example.invalid/x/pull/45/files"})
    assert pr_numbers(git) == ["45"]


async def test_fr_h0_12_follow_up_through_agent_service():
    service = AgentService(Router(), demo_mode=True, settings=Settings(_env_file=None, agent_engine="graph"))
    service.demo.command(1)
    context = service.demo.context(1)
    _, first = await service.handle("/brief", context)
    second_line = first.result.splitlines()[3]
    decision, answer = await service.handle("подробнее про 2-й пункт", context)
    assert decision.case == "brief_now" and decision.params["item"] == "2"
    assert answer.result == "Пункт 2 из брифа:\n" + second_line and answer.links == [first.links[1]]
    _, pr = await service.handle("что с PR #45?", context)
    assert pr.links == [PR45]
    _, missing = await service.handle("подробнее про 9-й пункт", context)
    assert missing.result == "В последнем брифе нет пункта 9." and missing.links == []
