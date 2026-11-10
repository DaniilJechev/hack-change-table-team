import json
from unittest.mock import AsyncMock

import pytest

from buddy.executors.core import (
    BudgetExceeded,
    Context,
    Registry,
    ToolScope,
    checked_response,
    render,
    template,
)
from buddy.mcp_hub.errors import ToolDenied
from buddy.mcp_hub.policy import CASE_TOOLS, READ_TOOLS
from buddy.schemas import Citation, ExecutorResponse

PROFILE = Context("demo-daniil", "demo-orders", "intern", True)


@pytest.mark.parametrize("case", sorted(Registry.cases))
async def test_st2_exec_1_every_case_has_explicit_stub(case):
    response = await Registry().execute(case, {}, Context("mentor", "demo-orders", "mentor"))
    assert response.result and not response.citations
    assert case in CASE_TOOLS


async def test_rt_05_denied_tool_and_identity_never_reach_gateway():
    gateway = AsyncMock()
    scope = ToolScope("team_q1", PROFILE, gateway)
    for server, tool, args in [
        ("jira", "jira_search", {"hidden-secret": "must-not-log"}),
        ("miro", "board_share", {}),
        ("buddy", "team_card_get", {"team_id": "other"}),
    ]:
        with pytest.raises(ToolDenied):
            await scope.call(server, tool, args)
    gateway.call.assert_not_called()
    assert all(row["outcome"] == "tool_denied" for row in scope.audit)
    assert "must-not-log" not in json.dumps(scope.audit)
    assert "jira_search" in json.dumps(scope.audit)


async def test_rt_05_fourth_call_stops_and_templates():
    gateway = AsyncMock()
    scope = ToolScope("team_q1", PROFILE, gateway)
    for _ in range(3):
        await scope.call("buddy", "team_card_get", {})
    with pytest.raises(BudgetExceeded):
        await scope.call("buddy", "team_card_get", {})
    assert gateway.call.await_count == 3
    assert scope.audit[-1]["outcome"] == "budget_exceeded"
    gateway.call.reset_mock()
    response, audit = await Registry().run_tools(
        "team_q1", PROFILE, gateway, [{"server": "buddy", "tool": "team_card_get", "arguments": {}}] * 4
    )
    assert gateway.call.await_count == 3 and audit[-1]["outcome"] == "budget_exceeded"
    assert response == template("team_q1")


async def test_st2_exec_1_intern_role_and_error_boundaries():
    gateway = AsyncMock()
    scope = ToolScope("interns_status", PROFILE, gateway)
    with pytest.raises(ToolDenied):
        await scope.call("buddy", "interns_list", {})
    gateway.call.assert_not_called()
    assert "только наставнику" in (await Registry().execute("interns_status", {}, PROFILE)).result
    assert (await Registry().execute("write_code", {}, PROFILE)) == template("out_of_scope")
    gateway.call.side_effect = RuntimeError("hidden-error")
    scope = ToolScope("team_q1", PROFILE, gateway)
    with pytest.raises(RuntimeError):
        await scope.call("buddy", "team_card_get", {})
    assert scope.audit[-1]["outcome"] == "unavailable"
    response, _ = await Registry().run_tools(
        "team_q1", PROFILE, gateway, [{"server": "buddy", "tool": "team_card_get", "arguments": {}}]
    )
    assert "hidden-error" not in response.result


def test_rt_05_policy_contains_read_tools_only():
    for servers in CASE_TOOLS.values():
        for server, tools in servers.items():
            assert set(tools) <= READ_TOOLS[server]
    assert "jira_search" not in CASE_TOOLS["team_q1"].get("jira", ())


@pytest.mark.parametrize(
    "raw",
    [
        "invalid",
        '{"result":"ok","extra":1}',
        '{"result":"fact","citations":[{"source_id":"invented"}]}',
        '{"result":"https://evil.invalid/private","citations":[]}',
        '{"result":"fact","citations":[]}',
        '{"result":"","citations":[]}',
        json.dumps({"result": "x" * 3001}),
    ],
)
def test_rt_06_invalid_answer_or_source_uses_template(raw):
    assert checked_response(raw, "team_q1", {}) == template("team_q1")


def test_rt_06_links_are_inserted_by_code_only():
    raw = '{"result":"Учебный ответ","citations":[{"source_id":"card"}]}'
    sources = {"card": "https://example.invalid/team/demo-orders/people"}
    response = checked_response(raw, "team_q1", sources)
    assert sources["card"] not in response.result
    assert render(response, sources).endswith(sources["card"])
    for url in ["https://example.invalid/", "file:///private", "https://user:secret@example.invalid/card"]:
        assert checked_response(raw, "team_q1", {"card": url}) == template("team_q1")
    assert checked_response('{"result":"help"}', "help", {}).result == "help"
    assert "подтверждённый" in render(ExecutorResponse(result="x", citations=[Citation(source_id="bad")]), {})
