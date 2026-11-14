"""FR-AG-10: every LLM call of a request is kept as data, so the graph state holds the full prompt.

The graph wraps each node: calls made while the node runs land in `CALLS` and are copied into the
state (`llm_calls`, readable as `turn`). The same calls are LangSmith `llm` runs when LANGSMITH_TRACING is on;
without it `traceable` does nothing. Only messages, tools and the reply are kept — never the API key.
"""

import functools
from contextvars import ContextVar
from copy import deepcopy

from langsmith import traceable

CALLS: ContextVar[list[dict] | None] = ContextVar("llm_calls", default=None)


def record(kind: str, request: dict, response, error: str = "") -> None:
    calls = CALLS.get()
    if calls is not None:
        calls.append(
            {"kind": kind, "request": deepcopy(request), "response": deepcopy(response), "error": error}
        )


def recorded(kind: str, *names: str):
    """Decorator for an LLM method: positional arguments are named, a failure is recorded and re-raised."""

    def wrap(method):
        @traceable(
            run_type="llm",
            name=f"llm.{kind}",
            process_inputs=lambda inputs: {k: v for k, v in inputs.items() if k != "self"},
        )
        @functools.wraps(method)
        async def run(self, *args, **kwargs):
            request = dict(zip(names, args, strict=False)) | kwargs
            try:
                response = await method(self, *args, **kwargs)
            except Exception as error:
                record(kind, request, None, type(error).__name__)
                raise
            record(kind, request, response)
            return response

        return run

    return wrap
