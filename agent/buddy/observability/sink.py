"""FR-TR-01: every graph turn goes to the backend (`agent_traces`) in the background.

A failed write never changes the answer: only the error kind is logged, never the turn's text.
"""

import asyncio
import logging
import uuid

import httpx

from buddy.data.backend import TIMEOUT, check_url

log = logging.getLogger(__name__)


def record(state) -> dict:
    turn, context = state["turn"], state["context"]
    return {
        "id": "tr-" + uuid.uuid4().hex,
        "user_id": context.user_id,
        "team_id": context.team_id,
        "role": context.role,
        "demo": context.demo,
        "path": turn["request"]["path"],
        "fallback": turn["fallback"],
        "case": turn["case"],
        "duration_ms": turn.get("duration_ms", 0),
        "llm_calls": len(state.get("llm_calls", [])),
        "tool_calls": turn["limits"]["tool_calls"],
        "turn": turn,
    }


class TraceSink:
    def __init__(self, url: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            base_url=check_url(url),
            headers={"X-Buddy-Key": key},
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.tasks: set[asyncio.Task] = set()

    def send(self, state) -> None:
        if not state.get("turn"):
            return
        task = asyncio.create_task(self._post(record(state)))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _post(self, body: dict) -> None:
        try:
            response = await self.client.post("/api/v1/agent-traces", json=body)
            if response.status_code != 201:
                log.warning("agent trace not saved: http %d", response.status_code)
        except (httpx.HTTPError, TypeError, ValueError) as error:
            log.warning("agent trace not saved: %s", type(error).__name__)

    async def close(self) -> None:
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
