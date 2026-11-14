"""FR-AG-10: the graph state keeps every LLM call and the full prompt; chat I/O of `langgraph dev`."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from buddy.agent import Agent
from buddy.agent.loop import SYSTEM
from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry
from buddy.graph import build_graph
from buddy.llm.client import CompatibleLLM, FakeLLM
from buddy.memory import Memory, Turn
from buddy.router.service import Router
from buddy.schemas import RouteDecision
from buddy.service import AgentService
from buddy.studio import Chat, build_studio_graph


def run_state(text: str, llm, agent_llm=None):
    controller = DemoController(True)
    controller.command(1)
    registry = Registry()
    agent = Agent(agent_llm, registry) if agent_llm is not None else None
    graph = build_graph(Router(llm), registry, Memory(), llm, agent)
    return graph.ainvoke({"text": text, "context": controller.context(1), "runtime": controller.runtime(1)})


async def test_fr_ag_10_state_keeps_full_agent_prompt():
    llm = FakeLLM(script=[{"content": "Привет! Чем помочь?"}])
    state = await run_state("привет", llm, agent_llm=llm)
    last = state["llm_calls"][-1]
    assert last["node"] == "agent" and last["kind"] == "chat" and "prompt" not in state
    prompt = last["request"]
    assert prompt["messages"][0] == {"role": "system", "content": SYSTEM}
    assert prompt["messages"][-1] == {"role": "user", "content": "привет"}
    assert {tool["function"]["name"] for tool in prompt["tools"]} >= {"brief_now", "playbook_qa"}
    assert llm.chats[0] == prompt["messages"]
    assert state["llm_calls"][0]["response"]["content"] == "Привет! Чем помочь?"


async def test_fr_ag_10_router_prompt_is_recorded_with_its_node():
    llm = FakeLLM()
    state = await run_state("кто такая Анна?", llm)
    router_calls = [call for call in state["llm_calls"] if call["node"] == "route"]
    assert router_calls and router_calls[0]["kind"] == "complete"
    assert "кто такая Анна?" in router_calls[0]["request"]["prompt"]
    assert state["llm_calls"][-1]["request"]["prompt"] == llm.calls[-1]


async def test_fr_ag_10_no_llm_call_leaves_no_prompt():
    state = await run_state("/brief", FakeLLM())
    assert not state.get("llm_calls") and "model_input" not in state["turn"]


async def test_fr_ag_10_failed_call_is_recorded_and_the_fallback_still_answers():
    llm = FakeLLM(script=[RuntimeError("boom")])
    state = await run_state("привет", llm, agent_llm=llm)
    failed = [call for call in state["llm_calls"] if call["error"]]
    assert failed[0]["error"] == "RuntimeError" and failed[0]["node"] == "agent"
    assert state["response"]


async def test_fr_ag_10_recorded_request_has_no_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Привет!"}}]})

    settings = Settings(
        _env_file=None, llm_provider="openrouter", llm_model="m", llm_api_key="secret-key-123"
    )
    llm = CompatibleLLM(settings, httpx.MockTransport(handler))
    state = await run_state("привет", llm, agent_llm=llm)
    assert "secret-key-123" not in json.dumps([state["llm_calls"], state["turn"]], ensure_ascii=False)
    assert state["llm_calls"][-1]["request"]["messages"][-1]["content"] == "привет"


def studio_chat_graph():
    service = AgentService(Router(FakeLLM()), demo_mode=True, llm=FakeLLM())
    graph = build_graph(
        service.router, service.executors, service.memory, service.llm, service.agent, Chat(service)
    )
    graph.checkpointer = InMemorySaver()  # the dev server brings its own
    return graph


async def test_fr_ag_10_studio_chat_answers_and_resets_each_turn():
    graph = studio_chat_graph()
    config = {"configurable": {"thread_id": "t1"}}
    first = await graph.ainvoke({"messages": [HumanMessage("кто такая Анна?")]}, config)
    assert isinstance(first["messages"][-1], AIMessage) and first["messages"][-1].content
    assert "runtime" not in first  # no live object in a checkpointed state
    assert first["turn"]["request"]["path"] == "router"
    assert [call["node"] for call in first["turn"]["other_llm_calls"]][:1] == ["route"]
    second = await graph.ainvoke({"messages": [HumanMessage("/access")]}, config)
    assert len(second["messages"]) == 4
    assert second["text"] == "/access" and second["llm_calls"] == []
    assert second["turn"]["other_llm_calls"] == [] and second["turn"]["request"]["text"] == "/access"
    assert len(await _history(graph, config)) > 6


async def _history(graph, config):
    return [snapshot async for snapshot in graph.aget_state_history(config)]


@pytest.mark.parametrize("persona,role", [("intern", "intern"), ("mentor", "mentor"), ("x", "intern")])
def test_fr_ag_10_studio_persona_is_chosen_by_config(persona, role):
    service = AgentService(Router(FakeLLM()), demo_mode=True, llm=FakeLLM())
    chat = Chat(service)
    context = chat.context({"configurable": {"persona": persona}})
    assert context.role == role and context.demo
    assert chat.runtime(context) is not None


def test_fr_ag_10_studio_graph_is_demo_only():
    graph = build_studio_graph(Settings(_env_file=None, demo_mode=True))
    assert {"inbox", "outbox", "agent", "route", "execute"} <= set(graph.get_graph().nodes)


def test_fr_ag_14_one_execute_node_replaces_the_family_nodes():
    nodes = set(build_studio_graph(Settings(_env_file=None, demo_mode=True)).get_graph().nodes)
    assert not nodes & {"brief", "playbook", "team", "access", "template"}


@pytest.mark.parametrize(
    "case,params,label",
    [
        ("brief_now", {}, "brief"),
        ("team_q7", {}, "team"),
        ("access_status", {}, "access"),
        ("help", {}, "template"),
    ],
)
async def test_fr_ag_14_trace_and_llm_calls_carry_the_case_family(case, params, label):
    controller = DemoController(True)
    controller.command(1)
    graph = build_graph(Router(), Registry(), Memory(), FakeLLM(), None)
    decision = RouteDecision(case=case, params=params)
    state = await graph.ainvoke(
        {
            "text": "x",
            "context": controller.context(1),
            "runtime": controller.runtime(1),
            "decision": decision,
        }
    )
    assert state["trace"] == [label]


async def seeded_followup(text: str, llm):
    controller = DemoController(True)
    controller.command(1)
    registry, memory, context = Registry(), Memory(), controller.context(1)
    await memory.save(context, Turn(case="team_q7", text="какой у нас стек?", answer="Go и Python."))
    graph = build_graph(Router(), registry, memory, llm, Agent(llm, registry, timeout=1))
    return await graph.ainvoke({"text": text, "context": context, "runtime": controller.runtime(1)})


async def test_fr_ag_11_agent_answers_a_followup_before_the_rules():
    llm = FakeLLM(script=[{"content": "Ещё могу рассказать про сервисы команды."}])
    state = await seeded_followup("ещё", llm)
    assert state["response"].result == "Ещё могу рассказать про сервисы команды."
    assert state["trace"] == ["agent"] and not state.get("followup")
    assert {"role": "assistant", "content": "Go и Python."} in llm.chats[0]


async def test_fr_ag_11_followup_rules_are_the_fallback_when_the_agent_fails():
    state = await seeded_followup("ещё", FakeLLM(script=[RuntimeError("boom")]))
    assert state["followup"] is True and state["decision"].case == "team_q7"
    assert state["trace"] == ["team"]
