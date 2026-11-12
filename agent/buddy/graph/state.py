from typing import Any, TypedDict

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
