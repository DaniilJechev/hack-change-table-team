from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from buddy.contracts import Context, ExecutorResponse, RouteDecision
from buddy.memory.store import Turn


class AgentState(TypedDict, total=False):
    """One request. `decision` set on input means a direct trigger (callback/scheduler): Router is skipped."""

    text: str
    context: Context
    runtime: Any
    decision: RouteDecision
    response: ExecutorResponse
    # FR-MEM-01: fresh turns of this user and team; `routed` is a standalone rewrite of a follow-up.
    turns: list[Turn]
    routed: str
    followup: bool
    # Case families that answered (`execute` adds its family), in order; for tests and audit only.
    trace: list[str]
    # FR-AG-10: every LLM call of this request ({node, kind, request, response, error}), raw.
    llm_calls: list[dict]
    # FR-AG-15: readable turn JSON; started uses time.monotonic().
    turn: dict | None
    started: float
    # Chat I/O of LangGraph Studio (`langgraph dev`); the Telegram path never sets it.
    messages: Annotated[list[AnyMessage], add_messages]
