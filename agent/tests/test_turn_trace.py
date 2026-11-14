"""FR-AG-15, FR-AG-16: the graph state keeps one readable JSON per turn (`turn`)."""

import json

from buddy.agent import Agent
from buddy.agent.loop import SYSTEM
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry
from buddy.graph import build_graph
from buddy.llm.client import FakeLLM
from buddy.memory import Memory, Turn
from buddy.router.service import Router
from buddy.schemas import RouteDecision


async def run(text: str, script=None, memory: Memory | None = None, **state):
    controller = DemoController(True)
    controller.command(1)
    llm = FakeLLM(script=list(script or []))
    registry = Registry()
    agent = Agent(llm, registry) if script is not None else None
    graph = build_graph(Router(llm), registry, memory or Memory(), llm, agent)
    context = controller.context(1)
    result = await graph.ainvoke(
        {"text": text, "context": context, "runtime": controller.runtime(1), **state}
    )
    return result["turn"], context


async def test_fr_ag_15_history_is_split_from_the_current_message():
    controller = DemoController(True)
    controller.command(1)
    memory = Memory()
    await memory.save(controller.context(1), Turn(case="chat", text="привет", answer="Привет! Чем помочь?"))
    turn, _ = await run("а что с брифом?", script=[{"content": "Сейчас посмотрю."}], memory=memory)
    model_input = turn["model_input"]
    assert model_input["system_prompt"] == SYSTEM
    assert model_input["trusted_context"] == {"role": "intern"}
    assert model_input["history"] == [
        {"role": "user", "text": "привет"},
        {"role": "assistant", "text": "Привет! Чем помочь?"},
    ]
    assert model_input["current_message"] == "а что с брифом?"
    assert "brief_now" in model_input["tools_offered"]


async def test_fr_ag_15_tool_step_lists_the_call_its_result_and_sources():
    script = [
        {"tool_calls": [{"id": "c1", "name": "brief_now", "arguments": "{}"}]},
        {"content": "Главное — замечания в PR #45 [S1]."},
    ]
    turn, _ = await run("что нового?", script=script)
    first, second = turn["steps"]
    call = first["tool_calls"][0]
    assert call["tool"] == "brief_now" and call["arguments"] == {}
    assert call["result"] and call["sources"][0] == "S1"
    assert second == {"step": 2, "model_text": "Главное — замечания в PR #45 [S1].", "tool_calls": []}
    assert turn["limits"] == {"model_calls": 2, "max_model_calls": 3, "tool_calls": 1, "max_tool_calls": 4}
    assert turn["request"] == {"text": "что нового?", "role": "intern", "path": "agent"}
    assert turn["fallback"] is False and turn["forced_refresh"] is False
    assert turn["answer"]["sources"][0]["n"] == 1 and turn["duration_ms"] >= 0


async def test_fr_ag_15_failed_agent_falls_back_to_the_router():
    turn, _ = await run("кто такая Анна?", script=[RuntimeError("boom")])
    assert turn["fallback"] is True and turn["request"]["path"] == "router"
    assert turn["steps"][0]["error"] == "RuntimeError"
    route = [call for call in turn["other_llm_calls"] if call["node"] == "route"]
    assert route and "кто такая Анна?" in route[0]["prompt"]


async def test_fr_ag_15_turn_without_llm_has_only_the_answer():
    turn, _ = await run("/brief")
    assert "model_input" not in turn and "steps" not in turn
    assert turn["answer"]["text"] and turn["answer"]["sources"]
    assert turn["other_llm_calls"] == [] and turn["limits"]["model_calls"] == 0


async def test_fr_ag_15_preset_decision_is_a_trigger():
    turn, _ = await run("", decision=RouteDecision.model_construct(case="brief_now", params={}))
    assert turn["request"]["path"] == "trigger" and turn["case"] == "brief_now"


async def test_fr_ag_16_internal_fields_stay_out_of_the_turn():
    turn, _ = await run("/brief")
    dumped = json.dumps(turn, ensure_ascii=False)
    assert turn["answer"]["buttons"] and all(isinstance(label, str) for label in turn["answer"]["buttons"])
    assert "entities" not in dumped and "brief_id" not in dumped and "brief:more:" not in dumped
