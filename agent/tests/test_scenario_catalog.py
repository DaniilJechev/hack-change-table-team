"""Regression checks for public execution boundaries after the structural migration."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from buddy.contracts import Context, ExecutorResponse, RouteDecision
from buddy.orchestration.agent_loop import tools_for
from buddy.orchestration.catalog import ROUTER_CASES, SCENARIOS
from buddy.orchestration.registry import Registry
from buddy.presentation import Action


def test_rt_01_catalog_matches_public_router_schema():
    enum = RouteDecision.model_json_schema()["properties"]["case"]["enum"]
    assert enum == list(ROUTER_CASES)
    assert len(enum) == 16
    assert {name for name in SCENARIOS if name not in enum} == {"brief_render", "access_events"}


def test_fr_ag_03_tool_schema_excludes_identity_and_internal_followups():
    for tool in tools_for("intern"):
        schema = tool["function"]["parameters"]
        assert schema["additionalProperties"] is False
        assert not {"user_id", "team_id", "role", "brief_id", "chunks"} & schema["properties"].keys()
    assert "interns_status" not in {t["function"]["name"] for t in tools_for("intern")}
    assert "interns_status" in {t["function"]["name"] for t in tools_for("mentor")}


async def test_rt_05_role_denied_before_injected_handler_runs():
    services = SimpleNamespace(access=AsyncMock(), team=AsyncMock(), brief=AsyncMock(), playbook=AsyncMock())
    registry = Registry(capabilities=services)
    response = await registry.execute("interns_status", {}, Context("u", "t", "intern"), object())
    assert "наставнику" in response.result
    services.access.interns.assert_not_called()


async def test_rt_04_scheduled_handler_uses_same_injected_capability():
    response = ExecutorResponse(result="Бриф")
    services = SimpleNamespace(access=AsyncMock(), team=AsyncMock(), brief=AsyncMock(), playbook=AsyncMock())
    services.brief.now_brief.return_value = response
    registry = Registry(capabilities=services)
    context, runtime = Context("u", "t", "intern"), object()
    assert await registry.trigger("brief_render", context, runtime) is response
    services.brief.now_brief.assert_awaited_once_with(context, runtime, source="", scheduled=True)


def test_fr_ui_02_content_projection_keeps_sources_without_delivery_controls():
    response = ExecutorResponse(
        result="Факт",
        links=["https://example.test/task/1"],
        entities={"task": ["ORD-1"]},
        actions=[Action(label="Открыть", url="https://example.test/task/1")],
        brief_id="b",
        feedback_saved=True,
    )
    content = response.content().model_dump()
    assert content["links"] == response.links and content["entities"] == response.entities
    assert not {"actions", "brief_id", "feedback_saved"} & content.keys()
    assert "feedback_saved" not in response.model_dump()


@pytest.mark.parametrize(
    "old,new",
    [
        ("buddy.agent.loop", "buddy.orchestration.agent_loop"),
        ("buddy.graph.build", "buddy.orchestration.graph"),
        ("buddy.service", "buddy.orchestration.service"),
        ("buddy.llm.trace", "buddy.observability.llm_trace"),
    ],
)
def test_st1_mcp_compat_1_legacy_import_is_same_module(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)
