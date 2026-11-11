"""The graph only orders steps; edges are chosen by code from the Router case, never by an LLM.

Executor nodes delegate to the existing business services through `Registry.execute`, so the graph
keeps every per-case tool whitelist and no-LLM fallback the services already have.
"""

import re
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from buddy.executors.core import Registry, template
from buddy.graph.state import AgentState
from buddy.memory import followup
from buddy.memory.store import Memory, Turn, summary_for_llm
from buddy.router.service import Router
from buddy.schemas import ExecutorResponse

FAMILIES = ("brief", "playbook", "team", "access", "template")
# Follow-up parameters produced by code from memory; a stored turn keeps only user-facing parameters.
INTERNAL = frozenset({"brief_id", "item", "pr", "chunks"})
ACCESS = frozenset({"access_status", "access_task_analysis", "interns_status", "access_events"})


def family(case: str) -> str:
    if case in {"brief_now", "brief_render"}:
        return "brief"
    if case == "playbook_qa":
        return "playbook"
    if case.startswith("team_q"):
        return "team"
    if case in ACCESS:
        return "access"
    return "template"


def safe_link(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme in {"https", "http"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not re.search(r"\s", url)
    )


def guarded(response: ExecutorResponse | None, case: str) -> ExecutorResponse:
    """Links are added by code: a citation without a trusted source or a broken link fails closed."""
    case = case if case in Registry.cases else "out_of_scope"
    if response is None:
        return template(case)
    if response.citations or not all(safe_link(link) for link in response.links):
        return template(case)
    if any(action.url and not safe_link(action.url) for action in response.actions):
        return template(case)
    return response


def build_graph(router: Router, registry: Registry, memory: Memory | None = None, llm=None):
    async def load_memory(state: AgentState) -> dict:
        if memory is None or state.get("decision") is not None:
            return {"turns": []}
        return {"turns": await memory.load(state["context"])}

    async def resolve_followup(state: AgentState) -> dict:
        turns = state.get("turns", [])
        if state.get("decision") is not None or not turns:
            return {}
        decision = followup.resolve(state.get("text", ""), turns)
        if decision is not None:
            return {"decision": decision, "followup": True}
        # Optional LLM rewrite: validated by schema, any failure leaves the Router on the original text.
        rewritten = await followup.rewrite(llm, state.get("text", ""), turns)
        return {"routed": rewritten} if rewritten else {}

    async def route(state: AgentState) -> dict:
        text = state.get("routed") or state.get("text", "")
        summary = summary_for_llm(state.get("turns", []))
        if summary == "{}":
            return {"decision": await router.route(text, context=state["context"])}
        return {"decision": await router.route(text, context=state["context"], memory=summary)}

    def executor(name: str):
        async def run(state: AgentState) -> dict:
            decision = state["decision"]
            text = state.get("routed") or state.get("text", "")
            response = await registry.execute(
                decision.case, decision.params, state["context"], state.get("runtime"), text
            )
            return {"response": response, "trace": [*state.get("trace", []), name]}

        run.__name__ = name
        return run

    async def guard(state: AgentState) -> dict:
        return {"response": guarded(state.get("response"), state["decision"].case)}

    async def save_memory(state: AgentState) -> dict:
        if memory is None:
            return {}
        decision, response = state["decision"], state["response"]
        case = "brief_now" if decision.case == "brief_render" else decision.case
        if case not in Registry.cases or case == "access_events":
            return {}
        text = state.get("routed") or state.get("text", "")
        params = {k: v for k, v in decision.params.items() if k not in INTERNAL}
        turn = Turn(
            case=case,
            params=params,
            entities=followup.entities(decision, text, response.entities),
            summary=response.result,
        )
        await memory.save(state["context"], turn)
        return {}

    graph = StateGraph(AgentState)
    graph.add_node("load_memory", load_memory)
    graph.add_node("resolve_followup", resolve_followup)
    graph.add_node("route", route)
    for name in FAMILIES:
        graph.add_node(name, executor(name))
        graph.add_edge(name, "guard")
    graph.add_node("guard", guard)
    graph.add_node("save_memory", save_memory)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "resolve_followup")
    # A preset decision is a direct trigger (button, scheduler): Router is not called.
    graph.add_conditional_edges(
        "resolve_followup",
        lambda state: family(state["decision"].case) if state.get("decision") else "route",
        ["route", *FAMILIES],
    )
    graph.add_conditional_edges("route", lambda state: family(state["decision"].case), list(FAMILIES))
    graph.add_edge("guard", "save_memory")
    graph.add_edge("save_memory", END)
    return graph.compile()
