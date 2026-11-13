"""LangGraph Studio entry point — a developer tool, not part of the bot.

    cd agent && uv run --extra studio langgraph dev

The Studio graph has the same nodes and edges as the production graph (`buddy.graph.build`) and runs
the very same node functions, but its state is plain JSON, so every step can be inspected:
the trusted context, memory turns, the Router decision, the executor response and — in `llm_calls` —
every prompt sent to the model together with its answer.

Data always come from the synthetic demo fixtures. Without `LLM_IN_DEMO=true` no model is called:
prompts are still built and recorded, and the answer is the no-LLM fallback (rules and templates).
"""

import hashlib
import time
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from buddy.config import Settings
from buddy.graph.build import FAMILIES, family
from buddy.llm.client import CompatibleLLM
from buddy.memory.store import Turn
from buddy.render import to_plain
from buddy.router.service import Router
from buddy.schemas import ExecutorResponse, RouteDecision
from buddy.service import AgentService

PURPOSES = (
    ("Ты Router", "router"),
    ("Ты формулируешь утренний бриф", "brief_wording"),
    ("Ты отвечаешь стажёру", "playbook_answer"),
)
MAX_CALLS = 200


class StudioInput(TypedDict, total=False):
    """What you type in Studio. `text` is the message; the rest selects the synthetic demo scenario."""

    text: str
    persona: Literal["intern", "mentor"]
    step: Literal[1, 2, 3]


class StudioState(StudioInput, total=False):
    context: dict
    turns: list[dict]
    routed: str
    followup: bool
    decision: dict | None
    response: dict | None
    trace: list[str]
    llm_calls: list[dict]
    memory: list[dict]
    answer: str


def purpose(prompt: str) -> str:
    return next((name for start, name in PURPOSES if prompt.lstrip().startswith(start)), "other")


class RecordingLLM:
    """Keeps the full prompt and the answer of every call; without an inner model returns the fallback."""

    def __init__(self, inner=None, model: str = ""):
        self.inner, self.model = inner, model if inner is not None else "none (fallback only)"
        self.calls: list[dict] = []

    async def complete(self, prompt: str, *, fallback: str) -> str:
        started = time.monotonic()
        answer = await self.inner.complete(prompt, fallback=fallback) if self.inner is not None else fallback
        self.calls.append(
            {
                "purpose": purpose(prompt),
                "model": self.model,
                "prompt": prompt,
                "prompt_chars": len(prompt),
                "response": answer,
                "fallback_returned": answer == fallback,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        del self.calls[:-MAX_CALLS]
        return answer


def dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [dump(item) for item in value]
    return value


def session_of(config) -> int:
    """One Studio thread is one isolated demo session with its own memory."""
    thread = str(((config or {}).get("configurable") or {}).get("thread_id") or "studio")
    return int(hashlib.sha256(thread.encode()).hexdigest()[:12], 16)


def load_settings() -> Settings:
    try:
        return Settings(demo_mode=True)
    except ValidationError:
        # LLM_IN_DEMO without a key, or live-only variables: Studio still works on fallbacks.
        return Settings(demo_mode=True, llm_in_demo=False, backend_url="", buddy_service_key="")


def build(settings: Settings | None = None, *, model=None):
    """`model` replaces the real LLM client (tests); by default it is used only with LLM_IN_DEMO=true."""
    settings = settings or load_settings()
    if model is None and settings.llm_in_demo:
        model = CompatibleLLM(settings)
    recorder = RecordingLLM(model, settings.llm_model)
    service = AgentService(Router(recorder), demo_mode=True, settings=settings, llm=recorder)
    inner = service.graph
    if inner is None:
        raise ValueError("Studio needs AGENT_ENGINE=graph")

    def actors(state: StudioState, config):
        session = session_of(config)
        demo = service.demo
        if not demo.active(session):
            demo.command(session)
        want_step = int(state.get("step") or demo.sessions[session].step + 1)
        if want_step - 1 != demo.sessions[session].step:
            demo.command(session, "reset")
            for _ in range(max(0, min(want_step, len(demo.steps)) - 1)):
                demo.command(session, "next")
        persona = state.get("persona")
        if persona in {"intern", "mentor"}:
            demo.command(session, persona)
        context = demo.context(session)
        return context, service.runtime(context)

    async def prepare(state: StudioState, config) -> dict:
        """Starts a new request in the thread: results of the previous message must not leak into it."""
        context, _ = actors(state, config)
        return {
            "context": {
                "user_id": context.user_id,
                "team_id": context.team_id,
                "role": context.role,
                "demo": True,
                "demo_step": service.demo.sessions[context.session].step + 1,
            },
            "turns": [],
            "routed": "",
            "followup": False,
            "decision": None,
            "response": None,
            "trace": [],
            "llm_calls": [],
            "memory": [],
            "answer": "",
        }

    def node(name: str):
        async def run(state: StudioState, config) -> dict:
            context, runtime = actors(state, config)
            inner_state = {
                "text": state.get("text", ""),
                "context": context,
                "runtime": runtime,
                "turns": [Turn.model_validate(item) for item in state.get("turns", [])],
                "routed": state.get("routed", ""),
                "followup": state.get("followup", False),
                "trace": state.get("trace", []),
            }
            if state.get("decision"):
                inner_state["decision"] = RouteDecision.model_validate(state["decision"])
            if state.get("response"):
                inner_state["response"] = ExecutorResponse.model_validate(state["response"])
            before = len(recorder.calls)
            update = await inner.nodes[name].bound.ainvoke(inner_state) or {}
            result = {key: dump(value) for key, value in update.items()}
            calls = recorder.calls[before:]
            if calls:
                result["llm_calls"] = [
                    *state.get("llm_calls", []),
                    *({"node": name, **call} for call in calls),
                ]
            if name == "save_memory":
                response = ExecutorResponse.model_validate(state["response"])
                result["memory"] = dump(await service.memory.load(context))
                result["answer"] = to_plain(response.result, response.folded, response.links)
            return result

        run.__name__ = name
        return run

    graph = StateGraph(StudioState, input_schema=StudioInput)
    graph.add_node("prepare", prepare)
    for name in ("load_memory", "resolve_followup", "route", *FAMILIES, "guard", "save_memory"):
        graph.add_node(name, node(name))
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "load_memory")
    graph.add_edge("load_memory", "resolve_followup")
    graph.add_conditional_edges(
        "resolve_followup",
        lambda state: family(state["decision"]["case"]) if state.get("decision") else "route",
        ["route", *FAMILIES],
    )
    graph.add_conditional_edges("route", lambda state: family(state["decision"]["case"]), list(FAMILIES))
    for name in FAMILIES:
        graph.add_edge(name, "guard")
    graph.add_edge("guard", "save_memory")
    graph.add_edge("save_memory", END)
    compiled = graph.compile()
    compiled.recorder, compiled.service = recorder, service  # handles for tests
    return compiled


_cached = None


def make_graph(config=None):
    """Factory referenced by `agent/langgraph.json`."""
    global _cached
    if _cached is None:
        _cached = build()
    return _cached
