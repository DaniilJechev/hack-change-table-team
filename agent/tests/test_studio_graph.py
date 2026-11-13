"""Developer tool: the LangGraph Studio graph mirrors the production graph with a JSON-only state."""

import json
from pathlib import Path

import pytest

from buddy.config import Settings
from buddy.graph import studio
from buddy.graph.build import FAMILIES
from buddy.router.service import SYSTEM

ROOT = Path(__file__).resolve().parents[1]


def settings(**extra) -> Settings:
    return Settings(demo_mode=True, llm_in_demo=False, _env_file=None, **extra)


def thread(name: str) -> dict:
    return {"configurable": {"thread_id": name}}


async def nodes_of(graph, text: str, config: dict) -> list[str]:
    return [
        name async for event in graph.astream({"text": text}, config, stream_mode="updates") for name in event
    ]


async def test_studio_state_is_json_and_shows_full_prompts_without_a_model():
    graph = studio.build(settings())
    state = await graph.ainvoke({"text": "Что такое Zero Bug Policy?"}, thread("a"))
    json.dumps(state, ensure_ascii=False)  # every field is serialisable for Studio
    assert state["context"] == {
        "user_id": "demo-daniil", "team_id": "demo-orders", "role": "intern", "demo": True, "demo_step": 1
    }  # fmt: skip
    assert state["decision"]["case"] == "playbook_qa" and state["trace"] == ["playbook"]
    router, playbook = state["llm_calls"]
    assert (router["node"], router["purpose"]) == ("route", "router")
    assert router["prompt"].startswith(SYSTEM) and "Что такое Zero Bug Policy?" in router["prompt"]
    assert router["prompt_chars"] == len(router["prompt"]) and router["fallback_returned"]
    assert router["model"] == "none (fallback only)"
    assert (playbook["node"], playbook["purpose"]) == ("playbook", "playbook_answer")
    assert "Zero Bug Policy" in playbook["prompt"] and "https://" not in playbook["prompt"]
    assert "development-principles.md" in state["answer"] and state["memory"][0]["case"] == "playbook_qa"


async def test_studio_mirrors_production_nodes_and_keeps_memory_per_thread():
    graph = studio.build(settings())
    assert set(graph.get_graph().nodes) - {"__start__", "__end__"} == {
        "prepare", "load_memory", "resolve_followup", "route", *FAMILIES, "guard", "save_memory"
    }  # fmt: skip
    first = await nodes_of(graph, "/brief", thread("b"))
    assert first == ["prepare", "load_memory", "resolve_followup", "route", "brief", "guard", "save_memory"]
    # A follow-up skips the Router; the previous decision of the thread does not leak into the new request.
    second = await nodes_of(graph, "подробнее про 2-й пункт", thread("b"))
    assert second == ["prepare", "load_memory", "resolve_followup", "brief", "guard", "save_memory"]
    state = await graph.ainvoke({"text": "Кто мой наставник?"}, thread("b"))
    assert state["decision"]["case"] == "team_q3" and not state["followup"] and len(state["memory"]) == 3
    other = await graph.ainvoke({"text": "подробнее про 2-й пункт"}, thread("c"))
    assert not other["followup"] and len(other["memory"]) == 1  # another thread has its own memory


async def test_studio_input_selects_demo_step_and_persona():
    graph = studio.build(settings())
    morning = await graph.ainvoke({"text": "/brief", "step": 3}, thread("d"))
    assert morning["context"]["demo_step"] == 3 and "12.11" in morning["answer"]
    mentor = await graph.ainvoke({"text": "/interns", "persona": "mentor", "step": 3}, thread("d"))
    assert mentor["context"]["role"] == "mentor" and mentor["decision"]["case"] == "interns_status"
    back = await graph.ainvoke({"text": "/interns", "persona": "intern", "step": 1}, thread("d"))
    assert back["context"] == {**back["context"], "role": "intern", "demo_step": 1}
    assert "только наставнику" in back["answer"]


async def test_studio_records_real_model_answers_when_a_model_is_given():
    class Model:
        async def complete(self, prompt: str, *, fallback: str) -> str:
            return '{"case": "team_q7", "params": {}}' if prompt.startswith("Ты Router") else fallback

    graph = studio.build(settings(llm_model="synthetic/model"), model=Model())
    state = await graph.ainvoke({"text": "Расскажи про наш стек"}, thread("e"))
    call = state["llm_calls"][0]
    assert state["decision"]["case"] == "team_q7" and call["model"] == "synthetic/model"
    assert call["response"] == '{"case": "team_q7", "params": {}}' and not call["fallback_returned"]
    assert studio.purpose("что-то другое") == "other"


def test_studio_settings_factory_and_langgraph_json(monkeypatch):
    monkeypatch.setenv("LLM_IN_DEMO", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    loaded = studio.load_settings()  # LLM_IN_DEMO without a key: Studio still starts on fallbacks
    assert loaded.demo_mode and not loaded.llm_in_demo
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    path, name = config["graphs"]["buddy"].split(":")
    assert (ROOT / path).is_file() and name == "make_graph"
    monkeypatch.setattr(studio, "_cached", None)
    monkeypatch.setattr(studio, "build", lambda: "graph")
    assert studio.make_graph() == "graph" and studio.make_graph({"x": 1}) == "graph"
    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    with pytest.raises(ValueError, match="AGENT_ENGINE=graph"):
        studio.__dict__["build"] = build_original
        build_original(settings(agent_engine="legacy"))


build_original = studio.build
