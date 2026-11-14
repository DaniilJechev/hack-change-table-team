"""FR-H0-UI-2: saved wording and a code-owned source format across the More boundary."""

import json

from tests.test_stage_four_brief import demo

from buddy.agent.loop import Agent
from buddy.brief.service import BriefService, rank
from buddy.llm.client import FakeLLM
from buddy.schemas import ExecutorResponse, RouteDecision


async def test_fr_h0_ui_2_partial_wording_more_repeat_and_restart_keep_format_and_links():
    _, runtime, context = demo()
    collector = BriefService()
    entries = rank((await collector.collect(context, runtime, ("github", "jira", "calendar")))[0].items)
    llm = FakeLLM(
        json.dumps(
            {
                "main": "Исправить замечания",
                "lines": [
                    {"item_id": entries[0].id, "text": "1. 🔴 Git: Нужны изменения в PR #45"},
                    {"item_id": entries[5].id, "text": "Jira: Срок задачи — завтра"},
                ],
            },
            ensure_ascii=False,
        )
    )
    service = BriefService(llm)
    initial = await service.now_brief(context, runtime)
    await service.delivered(initial.brief_id, runtime, True)
    more = await service.signal("more", initial.brief_id, context, runtime)
    assert "1. Git: Нужны изменения в PR #45" in initial.result
    assert "6. Jira: Срок задачи — завтра" in more.result
    assert len(initial.links) == 5 and len(more.links) == 3
    assert initial.actions[0].label == "Ещё 3"
    assert not any(icon in initial.result + more.result for icon in "🔴🟠🟡⚪🟢")
    repeated = await service.signal("more", initial.brief_id, context, runtime)
    assert repeated.result == more.result and len(llm.calls) == 1
    restarted = BriefService(FakeLLM("A new wording must never be requested"))
    restored = await restarted.signal("more", initial.brief_id, context, runtime)
    assert restored.result == more.result and restored.links == more.links
    assert restarted.llm.calls == []
    stored = await runtime.data.brief(context.user_id, initial.brief_id)
    assert stored["read_at"] and stored["items"][5]["render_text"] == "Срок задачи — завтра"


def test_fr_h0_ui_2_single_brief_agent_keeps_renderer_instead_of_rewording_again():
    decision = RouteDecision(case="brief_now")
    response = ExecutorResponse(result="1. Git: PR #45", links=["https://example.invalid/pull/45"])
    actual_decision, actual = Agent._compose(
        "Another final wording [S1]", response.links, [(decision, response)]
    )
    assert actual_decision == decision and actual == response


async def test_fr_h0_ui_2_legacy_brief_without_wording_uses_same_template_without_llm():
    _, runtime, context = demo()
    service = BriefService()
    response = await service.now_brief(context, runtime)
    expected = await service.signal("more", response.brief_id, context, runtime)
    # Simulate JSON written by the previous version, before optional render_text existed.
    stored = runtime.data._brief(context.user_id, response.brief_id)
    for item in stored["items"]:
        item.pop("render_text")
    restarted = BriefService(FakeLLM("Do not use the LLM for old briefs"))
    actual = await restarted.signal("more", response.brief_id, context, runtime)
    assert actual.result == expected.result and actual.links == expected.links
    assert restarted.llm.calls == []
