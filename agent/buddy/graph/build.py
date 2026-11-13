"""The graph only orders steps; edges are chosen by code from the Router case, never by an LLM.

Executor nodes delegate to the existing business services through `Registry.execute`, so the graph
keeps every per-case tool whitelist and no-LLM fallback the services already have.
"""

import logging
import re
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from buddy.agent import CHAT
from buddy.executors.core import PERSONAL, Registry, template
from buddy.graph.state import AgentState
from buddy.llm.trace import CALLS
from buddy.memory import followup
from buddy.memory.store import Memory, Turn, history, summary_for_llm
from buddy.render import cite
from buddy.router.service import Router
from buddy.schemas import ExecutorResponse, RouteDecision

FAMILIES = ("brief", "playbook", "team", "access", "template")
# Follow-up parameters produced by code from memory; a stored turn keeps only user-facing parameters.
INTERNAL = frozenset({"brief_id", "item", "pr", "chunks", "items"})
ACCESS = frozenset({"access_status", "access_task_analysis", "interns_status", "access_events"})
log = logging.getLogger(__name__)


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
    result, folded, links = cite(response.result, response.folded, response.links)  # FR-UI-03
    return response.model_copy(update={"result": result, "folded": folded, "links": links})


def agent_text(text: str) -> bool:
    """FR-AG-02: commands and empty or oversized texts keep the rules path."""
    return bool(text.strip()) and not text.lstrip().startswith("/") and len(text) <= 4000


def message_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)


def traced(name: str, node):
    """FR-AG-10: LLM calls made while the node runs are copied into the state with the node's name."""

    async def run(state: AgentState) -> dict:
        calls: list[dict] = []
        token = CALLS.set(calls)
        try:
            update = await node(state)
        finally:
            CALLS.reset(token)
        if not calls:
            return update
        last = calls[-1]
        return {
            **update,
            "llm_calls": [*state.get("llm_calls", []), *({"node": name, **call} for call in calls)],
            "prompt": {"node": name, "kind": last["kind"], **last["request"]},
        }

    return run


def build_graph(
    router: Router, registry: Registry, memory: Memory | None = None, llm=None, agent=None, chat=None
):
    """`chat` (LangGraph Studio) adds inbox/outbox nodes: the last human message in, the answer out.
    It provides `context(config)` and `runtime(context)`, so no live object is kept in the state."""

    def rt(state: AgentState):
        return state.get("runtime") or (chat.runtime(state["context"]) if chat else None)

    async def load_memory(state: AgentState) -> dict:
        if memory is None or state.get("decision") is not None:
            return {"turns": []}
        return {"turns": await memory.load(state["context"])}

    async def resolve_followup(state: AgentState) -> dict:
        turns = state.get("turns", [])
        if state.get("decision") is not None or not turns:
            return {}
        decision = followup.resolve(state.get("text", ""), turns, titles=registry.brief.titles)
        if decision is not None:
            return {"decision": decision, "followup": True}
        # Optional LLM rewrite: validated by schema, any failure leaves the Router on the original text.
        rewritten = await followup.rewrite(llm, state.get("text", ""), turns)
        return {"routed": rewritten} if rewritten else {}

    async def run_agent(state: AgentState) -> dict:
        text = state.get("text", "")
        if PERSONAL.search(text.casefold()):
            # FR-AG-02: salary, personal and medical data — the HR BP refusal by code, no LLM.
            decision = RouteDecision(case="out_of_scope")
            return {"decision": decision, "response": template("out_of_scope", text=text)}
        result = await agent.answer(text, state["context"], rt(state), history(state.get("turns", [])))
        if result is None:
            # FR-AG-05, FR-AG-11: no tool ran and the model failed — the follow-up rules, then the Router.
            decision = followup.resolve(text, state.get("turns", []), titles=registry.brief.titles)
            return {"decision": decision, "followup": True} if decision is not None else {}
        decision, response = result
        return {"decision": decision, "response": response, "trace": [*state.get("trace", []), "agent"]}

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
                decision.case, decision.params, state["context"], rt(state), text
            )
            return {"response": response, "trace": [*state.get("trace", []), name]}

        run.__name__ = name
        return run

    async def guard(state: AgentState) -> dict:
        response = guarded(state.get("response"), state["decision"].case)
        # One line per turn: what answered and how; never the user's text or the model's output.
        log.info(
            "turn: case=%s trace=%s llm_calls=%d links=%d",
            state["decision"].case,
            ",".join(state.get("trace", [])),
            len(state.get("llm_calls", [])),
            len(response.links),
        )
        return {"response": response}

    async def save_memory(state: AgentState) -> dict:
        if memory is None:
            return {}
        decision, response = state["decision"], state["response"]
        case = "brief_now" if decision.case == "brief_render" else decision.case
        if (case not in Registry.cases and case != CHAT) or case == "access_events":
            return {}
        text = state.get("routed") or state.get("text", "")
        params = {k: v for k, v in decision.params.items() if k not in INTERNAL}
        turn = Turn(
            case=case,
            params=params,
            entities=followup.entities(decision, text, response.entities),
            summary=response.result,
            # FR-MEM-04: the user's own words and the answer as shown, after guard.
            text=state.get("text", ""),
            answer=response.result,
        )
        await memory.save(state["context"], turn)
        return {}

    async def inbox(state: AgentState, config: RunnableConfig) -> dict:
        human = next((m for m in reversed(state.get("messages", [])) if m.type == "human"), None)
        # A thread keeps its state between turns: everything of the previous request is reset here.
        return {
            "text": message_text(human) if human else "",
            "context": chat.context(config),
            "decision": None,
            "response": None,
            "routed": "",
            "followup": False,
            "trace": [],
            "llm_calls": [],
            "prompt": None,
        }

    async def outbox(state: AgentState) -> dict:
        response = state["response"]
        links = "".join(f"\n[{number}] {url}" for number, url in enumerate(response.links, 1))
        return {"messages": [AIMessage(content=response.result + links)]}

    graph = StateGraph(AgentState)
    graph.add_node("load_memory", traced("load_memory", load_memory))
    graph.add_node("resolve_followup", traced("resolve_followup", resolve_followup))
    graph.add_node("route", traced("route", route))
    graph.add_node("agent", traced("agent", run_agent))
    for name in FAMILIES:
        graph.add_node(name, traced(name, executor(name)))
        graph.add_edge(name, "guard")
    graph.add_node("guard", guard)
    graph.add_node("save_memory", save_memory)

    if chat is None:
        graph.add_edge(START, "load_memory")
    else:
        graph.add_node("inbox", inbox)
        graph.add_node("outbox", outbox)
        graph.add_edge(START, "inbox")
        graph.add_edge("inbox", "load_memory")

    # FR-AG-11: text for the agent skips the follow-up rules: the agent reads the dialog itself and
    # answers in its own words; the rules wait as its fallback (see run_agent). No rewrite is needed.
    def after_memory(state: AgentState) -> str:
        if state.get("decision") is None and agent is not None and agent_text(state.get("text", "")):
            return "agent"
        return "resolve_followup"

    graph.add_conditional_edges("load_memory", after_memory, ["agent", "resolve_followup"])

    # A preset decision is a direct trigger (button, scheduler): Router is not called.
    def after_followup(state: AgentState) -> str:
        if state.get("decision"):
            return family(state["decision"].case)
        return "route"  # text for the agent never gets here: after_memory sends it to the agent directly

    graph.add_conditional_edges("resolve_followup", after_followup, ["route", *FAMILIES])

    def after_agent(state: AgentState) -> str:
        if state.get("response"):
            return "guard"
        return family(state["decision"].case) if state.get("decision") else "route"

    graph.add_conditional_edges("agent", after_agent, ["guard", "route", *FAMILIES])
    graph.add_conditional_edges("route", lambda state: family(state["decision"].case), list(FAMILIES))
    graph.add_edge("guard", "save_memory")
    if chat is None:
        graph.add_edge("save_memory", END)
    else:
        graph.add_edge("save_memory", "outbox")
        graph.add_edge("outbox", END)
    return graph.compile()
