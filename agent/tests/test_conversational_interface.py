from unittest.mock import AsyncMock

import pytest
from test_agent_orchestrator import setup
from test_stage_two_bot import Harness

from buddy.bot.rendering import response_menu
from buddy.orchestration.registry import template
from buddy.orchestration.router.service import Router
from buddy.prompts.commands import GUIDE, referenced_command


async def test_fr_conv_01_help_demo_and_start_do_not_promote_ask():
    app = Harness()
    try:
        for text in ("/start", "/help", "/demo"):
            assert "/ask" not in (await app.send(text)).text
        legacy = await app.send("/ask")
        assert len(legacy.reply_markup.inline_keyboard) == 8
        assert response_menu("help") is None
    finally:
        await app.bot.session.close()


async def test_fr_conv_02_llm_clarifies_without_type_menu():
    ask, _, registry, _, _ = setup({"content": "Вас интересует время брифа или его содержимое?"})
    registry.execute = AsyncMock(side_effect=AssertionError("No tool needed"))
    state = await ask("Хочу разобраться с брифом")
    assert state["response"].result.endswith("?")
    assert not state["response"].actions
    assert response_menu(state["decision"].case, state["response"]) is None


async def test_fr_conv_02_failed_llm_keeps_router_fallback():
    ask, _, _, _, _ = setup(RuntimeError("offline"))
    state = await ask("непонятная фраза")
    assert state["decision"].case == "clarify"
    assert state["response"].actions


async def test_fr_conv_03_settings_question_uses_help_without_reading_or_writing():
    ask, llm, registry, runtime, context = setup({"content": "Настрой время: /settings brief 09:00."})
    before = await runtime.data.brief_settings(context.user_id)
    registry.execute = AsyncMock(side_effect=AssertionError("No product tool"))
    runtime.data.team_card = AsyncMock(side_effect=AssertionError("No team lookup"))
    state = await ask("Как настроить /settings?")
    assert "/settings brief 09:00" in state["response"].result
    assert await runtime.data.brief_settings(context.user_id) == before
    registry.execute.assert_not_called()
    runtime.data.team_card.assert_not_called()
    assert llm.choices[0][0] == "none"


async def test_fr_conv_03_settings_help_fallback_has_exact_syntax():
    ask, _, _, _, _ = setup(RuntimeError("offline"))
    state = await ask("Что делает /settings?")
    assert "/settings brief HH:MM" in state["response"].result
    assert "/settings quiet on|off" in state["response"].result
    route = await Router().route("Что делает /settings?")
    assert template(route.case, route.params).result == state["response"].result


async def test_fr_conv_03_direct_settings_command_keeps_existing_handler():
    app = Harness()
    try:
        await app.send("/demo")
        await app.send("/settings quiet on")
        context = app.service.demo.context(42)
        current = await app.service.runtime(context).data.brief_settings(context.user_id)
        assert current["quiet"] is True
    finally:
        await app.bot.session.close()


@pytest.mark.parametrize("text", ["/settings quiet on", "Что нового? /brief", "Как работает Kafka?"])
def test_fr_conv_03_command_help_does_not_capture_other_requests(text):
    assert referenced_command(text) is None


def test_fr_conv_03_both_prompts_include_shared_command_guide():
    from buddy.prompts.agent import SYSTEM as agent
    from buddy.prompts.router import SYSTEM as router

    assert GUIDE in agent and GUIDE in router
