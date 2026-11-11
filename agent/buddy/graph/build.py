"""The graph only orders steps; edges are chosen by code from the Router case, never by an LLM.

Executor nodes delegate to the existing business services through `Registry.execute`, so the graph
keeps every per-case tool whitelist and no-LLM fallback the services already have.
"""

import re
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from buddy.executors.core import Registry, template
from buddy.graph.state import AgentState
from buddy.router.service import Router
from buddy.schemas import ExecutorResponse

FAMILIES = ("brief", "playbook", "team", "access", "template")
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


def build_graph(router: Router, registry: Registry):
    async def load_memory(state: AgentState) -> dict:
        # Stage 2 (FR-MEM-01): the dialog window is loaded here. Without memory the step is a no-op.
        return {}

    async def resolve_followup(state: AgentState) -> dict:
        # Stage 2 (FR-MEM-05): short continuations are resolved here. Without memory the step is a no-op.
        return {}

    async def route(state: AgentState) -> dict:
        return {"decision": await router.route(state.get("text", ""), context=state["context"])}

    def executor(name: str):
        async def run(state: AgentState) -> dict:
            decision = state["decision"]
            response = await registry.execute(
                decision.case, decision.params, state["context"], state.get("runtime"), state.get("text", "")
            )
            return {"response": response, "trace": [*state.get("trace", []), name]}

        run.__name__ = name
        return run

    async def guard(state: AgentState) -> dict:
        return {"response": guarded(state.get("response"), state["decision"].case)}

    async def save_memory(state: AgentState) -> dict:
        # Stage 2 (FR-MEM-01): the turn is stored here. Without memory the step is a no-op.
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
