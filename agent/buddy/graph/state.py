from typing import Any, TypedDict

from buddy.executors.context import Context
from buddy.schemas import ExecutorResponse, RouteDecision


class AgentState(TypedDict, total=False):
    """One request. `decision` set on input means a direct trigger (callback/scheduler): Router is skipped."""

    text: str
    context: Context
    runtime: Any
    decision: RouteDecision
    response: ExecutorResponse
    # Executor families visited, in order; used by tests and audit, never shown to the user.
    trace: list[str]
