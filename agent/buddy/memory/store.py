"""FR-MEM-01/02/06: a short dialog (last N turns) per trusted user and team, kept in process or in Redis."""

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from buddy.executors.context import Context

# Defaults; live values come from MEMORY_MAX_TURNS / MEMORY_TTL_SECONDS (see config.py).
# A "turn" is one handled request: the user's text plus the answer (two chat messages).
DEFAULT_MAX_TURNS = 6
DEFAULT_TTL_SECONDS = 30 * 60
log = logging.getLogger(__name__)

# FR-MEM-04: tokens, keys and authorization headers never reach the memory.
SECRETS = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}"
    r"|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
    r"|(?i:bearer|basic|token|api[_-]?key|password|пароль)\s*[:=]?\s*\S{6,}|[A-Za-z0-9+/_-]{40,})"
)


ENTITY = re.compile(r"[A-Za-z0-9_.#:-]{1,100}")
TOKEN_PREFIX = re.compile(r"(gh[pousr]_|github_pat_|sk-|xox[abprs]-|AKIA|eyJ)")


def redact(value: str, limit: int = 300) -> str:
    return SECRETS.sub("[скрыто]", value)[:limit]


def entity(value: str) -> bool:
    """Entities are object IDs (task key, PR, item_id, chunk_id): anything else is dropped, not stored."""
    return bool(ENTITY.fullmatch(value)) and not TOKEN_PREFIX.match(value)


class Turn(BaseModel):
    """One handled request: case, parameters, entities, a short summary and — FR-MEM-04 — the user's
    text and the answer shown to the user (checked by code), never an unchecked LLM output."""

    case: str
    params: dict[str, str] = Field(default_factory=dict)
    entities: dict[str, list[str]] = Field(default_factory=dict)
    summary: str = ""
    text: str = ""
    answer: str = ""
    role: str = ""
    at: float = 0

    def clean(self) -> "Turn":
        return self.model_copy(
            update={
                "params": {k: redact(v) for k, v in self.params.items()},
                "entities": {k: [item for item in v[:20] if entity(item)] for k, v in self.entities.items()},
                "summary": redact(self.summary, 200),
                "text": redact(self.text, 600),
                "answer": redact(self.answer, 600),
            }
        )


def history(turns: list[Turn]) -> list[dict]:
    """FR-MEM-04: dialog replies for the agent; the Router summary stays without them."""
    messages = []
    for turn in turns:
        if turn.text and turn.answer:
            messages += [
                {"role": "user", "content": turn.text},
                {"role": "assistant", "content": turn.answer},
            ]
    return messages


def key(context: Context) -> str:
    """Only the trusted profile identifies memory: never IDs from the message text or an LLM answer."""
    if context.demo:
        return f"demo:{context.session}:{context.team_id}:{context.user_id}"
    return f"live:{context.team_id}:{context.user_id}"


class Store(Protocol):
    async def load(self, name: str) -> list[Turn]: ...

    async def append(self, name: str, turn: Turn) -> None: ...

    async def clear(self, name: str) -> None: ...


class InMemoryStore:
    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS):
        self.max_turns = max_turns
        self.data: dict[str, list[Turn]] = {}

    async def load(self, name: str) -> list[Turn]:
        return list(self.data.get(name, []))

    async def append(self, name: str, turn: Turn) -> None:
        turns = self.data.setdefault(name, [])
        turns.append(turn)
        del turns[: -self.max_turns]

    async def clear(self, name: str) -> None:
        self.data.pop(name, None)

    def clear_prefix(self, prefix: str) -> None:
        for name in [name for name in self.data if name.startswith(prefix)]:
            self.data.pop(name, None)


class RedisStore:
    """Live mode (FR-MEM-06): list per user with LTRIM to max_turns and EXPIRE as the TTL."""

    def __init__(
        self,
        client,
        max_turns: int = DEFAULT_MAX_TURNS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        prefix: str = "buddy:memory:",
    ):
        self.client, self.max_turns, self.ttl_seconds, self.prefix = client, max_turns, ttl_seconds, prefix

    @classmethod
    def from_url(
        cls, url: str, max_turns: int = DEFAULT_MAX_TURNS, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> "RedisStore":  # pragma: no cover - needs a Redis server
        from redis.asyncio import Redis

        client = Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1, decode_responses=True)
        return cls(client, max_turns, ttl_seconds)

    async def load(self, name: str) -> list[Turn]:
        turns = []
        for raw in await self.client.lrange(self.prefix + name, 0, -1):
            try:
                turns.append(Turn.model_validate_json(raw))
            except ValidationError:
                continue
        return turns

    async def append(self, name: str, turn: Turn) -> None:
        name = self.prefix + name
        pipe = self.client.pipeline()
        pipe.rpush(name, turn.model_dump_json())
        pipe.ltrim(name, -self.max_turns, -1)
        pipe.expire(name, self.ttl_seconds)
        await pipe.execute()

    async def clear(self, name: str) -> None:
        await self.client.delete(self.prefix + name)


class Memory:
    """Turn limit, TTL, role filter and fail-open behaviour on top of a store.

    Demo sessions always use their own in-process store, so the demo never needs Redis.
    """

    def __init__(
        self,
        store: Store | None = None,
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        enabled: bool = True,
    ):
        self.store = store or InMemoryStore(max_turns)
        self.demo_store = InMemoryStore(max_turns)
        self.max_turns, self.ttl_seconds, self.clock, self.enabled = max_turns, ttl_seconds, clock, enabled

    def _store(self, context: Context) -> Store:
        return self.demo_store if context.demo else self.store

    async def load(self, context: Context) -> list[Turn]:
        if not self.enabled:
            return []
        try:
            turns = await self._store(context).load(key(context))
        except Exception:
            log.warning("memory unavailable, answering without it")
            return []
        fresh = self.clock() - self.ttl_seconds
        # A role change (demo mentor/intern switch, profile update) makes earlier turns unusable.
        return [turn for turn in turns if turn.at >= fresh and turn.role == context.role][-self.max_turns :]

    async def save(self, context: Context, turn: Turn) -> None:
        if not self.enabled:
            return
        try:
            await self._store(context).append(
                key(context), turn.clean().model_copy(update={"role": context.role, "at": self.clock()})
            )
        except Exception:
            log.warning("memory unavailable, turn not saved")

    async def clear(self, context: Context) -> None:
        try:
            await self._store(context).clear(key(context))
        except Exception:
            log.warning("memory unavailable, nothing cleared")

    def clear_demo(self, session: int) -> None:
        self.demo_store.clear_prefix(f"demo:{session}:")


def summary_for_llm(turns: list[Turn]) -> str:
    """FR-MEM-04 / RT-08: trusted summary built by code, passed to an LLM only as escaped JSON data."""
    if not turns:
        return "{}"
    last = turns[-1]
    summary: dict = {"case": last.case, "entities": last.entities}
    # FR-MEM-04: the latest brief stays visible to the Router a few turns later (IDs only).
    brief = next((t for t in reversed(turns) if t.case == "brief_now" and t.entities.get("brief")), None)
    if brief is not None and brief is not last:
        summary["brief"] = {k: brief.entities[k] for k in ("brief", "pr", "task") if k in brief.entities}
    return json.dumps(summary, ensure_ascii=False)
