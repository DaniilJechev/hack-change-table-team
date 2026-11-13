from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from buddy.executors.context import Context
from buddy.memory.store import Turn
from buddy.schemas import ExecutorResponse, RouteDecision


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
    # Executor families visited, in order; used by tests and audit, never shown to the user.
    trace: list[str]
    # FR-AG-10: every LLM call of this request ({node, kind, request, response, error}) and the last
    # request exactly as the model saw it (system prompt, trusted context, history, tool schemas).
    llm_calls: list[dict]
    prompt: dict | None
    # Chat I/O of LangGraph Studio (`langgraph dev`); the Telegram path never sets it.
    messages: Annotated[list[AnyMessage], add_messages]
