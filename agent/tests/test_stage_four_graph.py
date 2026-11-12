"""ST4-GRAPH-1/2: deterministic LangGraph engine and parity with the legacy Router → Executor pipeline."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from buddy.config import Settings
from buddy.executors.core import format_response, template
from buddy.graph import FAMILIES, family
from buddy.graph.build import guarded
from buddy.router.service import Router
from buddy.schemas import Action, Citation, ExecutorResponse
from buddy.service import AgentService

ROOT = Path(__file__).resolve().parents[2]


def demo_service(engine: str) -> tuple[AgentService, object]:
    # ST4-GRAPH-2: parity is defined with memory off; follow-ups are covered by test_stage_four_memory.
    settings = Settings(_env_file=None, agent_engine=engine, memory_enabled=False)
    service = AgentService(Router(), demo_mode=True, settings=settings)
    service.demo.command(1)
    return service, service.demo.context(1)


def phrases() -> list[str]:
    texts = []
    for name in ("router/cases.jsonl", "router/holdout.jsonl", "h2/cases.jsonl", "h3/cases.jsonl"):
        for line in (ROOT / "evals" / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["text"])
    return texts


def shown(response: ExecutorResponse) -> str:
    # Brief and rated-answer IDs are random per record; everything else must match byte for byte.
    raw = json.dumps(
        [format_response(response), [action.model_dump() for action in response.actions]],
        ensure_ascii=False,
    )
    return re.sub(r"\b(br|b)-[0-9a-f]{12,16}\b", r"\1-<id>", raw)


def test_st4_graph_1_family_covers_every_case():
    assert family("team_q2") == "team"
    assert family("brief_render") == "brief"
    assert family("access_events") == "access"
    assert family("playbook_qa") == "playbook"
    assert family("help") == family("unknown_case") == "template"
    assert set(FAMILIES) == {"brief", "playbook", "team", "access", "template"}


async def test_st4_graph_1_executor_chosen_by_code():
    service, context = demo_service("graph")
    service.executors.team.answer = AsyncMock(return_value=ExecutorResponse(result="team"))
    service.executors.brief.now_brief = AsyncMock()
    service.executors.playbook.answer = AsyncMock()
    state = await service.graph.ainvoke(
        {"text": "Упал staging, кому писать?", "context": context, "runtime": service.runtime(context)}
    )
    assert state["decision"].case == "team_q2"
    assert state["trace"] == ["team"]
    service.executors.brief.now_brief.assert_not_called()
    service.executors.playbook.answer.assert_not_called()


async def test_st4_graph_1_direct_trigger_skips_router():
    service, context = demo_service("graph")
    service.router.route = AsyncMock()
    response = await service.trigger("brief_render", context)
    service.router.route.assert_not_called()
    assert response.result


def test_st4_graph_1_guard_rejects_unknown_source():
    fake = ExecutorResponse(result="Ответ", citations=[Citation(source_id="made-up")])
    assert guarded(fake, "team_q1") == template("team_q1")
    assert guarded(ExecutorResponse(result="x", links=["javascript:alert(1)"]), "team_q1") == template(
        "team_q1"
    )
    assert guarded(
        ExecutorResponse(result="x", actions=[Action(label="go", url="https://u:p@evil.test/x")]), "help"
    ) == template("help")
    assert guarded(None, "write_code") == template("out_of_scope")
    ok = ExecutorResponse(result="x", links=["https://github.com/avito-tech/playbook/blob/main/a.md#b"])
    assert guarded(ok, "playbook_qa") is ok


async def test_st4_graph_1_demo_runs_without_network(monkeypatch):
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("network call in demo")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    service, context = demo_service("graph")
    for text in ("/brief", "Какие у меня доступы?", "Кто мой наставник?", "Что такое Zero Bug Policy?"):
        _, response = await service.handle(text, context)
        assert response.result


async def test_st4_graph_2_engines_give_identical_answers():
    legacy, legacy_context = demo_service("legacy")
    graph, graph_context = demo_service("graph")
    assert legacy.graph is None and graph.graph is not None
    for text in phrases():
        old_decision, old = await legacy.handle(text, legacy_context)
        new_decision, new = await graph.handle(text, graph_context)
        assert new_decision == old_decision, text
        assert shown(new) == shown(old), text
    for case in ("brief_render", "access_events"):
        assert shown(await graph.trigger(case, graph_context)) == shown(
            await legacy.trigger(case, legacy_context)
        )


def test_st4_graph_2_unknown_engine_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_engine="react")
    monkeypatch.setenv("AGENT_ENGINE", "react")
    with pytest.raises(ValueError, match="AGENT_ENGINE"):
        AgentService(Router(), demo_mode=True)
