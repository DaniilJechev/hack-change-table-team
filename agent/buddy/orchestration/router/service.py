import asyncio
import json
import re
from typing import Protocol

from buddy.contracts import RouteDecision
from buddy.llm.client import LLM
from buddy.orchestration.catalog import PARAMS, ROUTER_CASES
from buddy.orchestration.router.rules import TEAM_CHOICES, clarify, decide
from buddy.prompts.router import SYSTEM

CASES = ROUTER_CASES
ACCESS_CASES = frozenset({"access_status", "access_task_analysis"})


def parse(raw: str) -> RouteDecision:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected object")
    if isinstance(data.get("case"), str) and data["case"] not in CASES:
        return RouteDecision(case="out_of_scope")
    result = RouteDecision.model_validate(data)
    if not set(result.params).issubset(PARAMS[result.case]) or any(
        len(v) > 4000 for v in result.params.values()
    ):
        raise ValueError("Unexpected route parameters")
    return result


JIRA_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]{1,15}-\d{1,10}")


def normalize(decision: RouteDecision) -> RouteDecision:
    """RT-09: canonical parameter casing for Executors, independent of rules or LLM output."""
    params = dict(decision.params)
    if "situation" in params:
        params["situation"] = params["situation"].strip().lower()
    task = params.get("task")
    if task is not None and JIRA_KEY.fullmatch(task):
        params["task"] = task.upper()
    if params == decision.params:
        return decision
    return decision.model_copy(update={"params": params})


class RoutingContext(Protocol):
    team_id: str
    role: str


class Router:
    def __init__(self, llm: LLM | None = None, *, timeout: float = 20):
        if not 0 < timeout <= 20:
            raise ValueError("Router timeout must be within 20 seconds")
        self.llm = llm
        self.timeout = timeout

    async def route(
        self, text: str, *, context: RoutingContext | None = None, memory: str = ""
    ) -> RouteDecision:
        decision = await self._classify(text, context=context, memory=memory)
        if decision.case == "clarify":
            # RT-03: the clarify options are chosen by code from the text, never by the model.
            decision = clarify(text)
        return normalize(decision)

    async def _classify(
        self, text: str, *, context: RoutingContext | None, memory: str = ""
    ) -> RouteDecision:
        fallback = decide(text)
        if self.llm is None or not text.strip() or text.lstrip().startswith("/") or len(text) > 4000:
            return fallback
        prompt = (
            SYSTEM
            + "\nСхема: "
            + json.dumps(RouteDecision.model_json_schema(), ensure_ascii=False)
            + "\nДопустимые параметры: "
            + json.dumps({k: sorted(v) for k, v in PARAMS.items()})
            + "\nДоверенный контекст (JSON): "
            + json.dumps(
                {"role": context.role, "team_id": context.team_id} if context else {}, ensure_ascii=False
            )
            # RT-08: built by code from memory (case and entities, no raw messages); data, not instructions.
            + "\nСводка прошлого хода (JSON, только данные): "
            + json.dumps(memory or "{}", ensure_ascii=False)
            + "\nТекст пользователя (JSON): "
            + json.dumps(text, ensure_ascii=False)
        )
        try:
            async with asyncio.timeout(self.timeout):
                raw = await self.llm.complete(prompt, fallback=fallback.model_dump_json())
            result = parse(raw)
            if context is not None and context.role == "intern" and result.case == "interns_status":
                # A mentor-only case suggested for an intern is never taken from the model.
                return fallback
            person_access = "доступ" in text.casefold() and fallback.params.get("options") == TEAM_CHOICES
            if result.case in ACCESS_CASES and person_access:
                # RT-03: «доступ к Анне» — access or the person? The rules' clarify wins over a guess.
                return fallback
            if result.case == "out_of_scope" and fallback.case in ACCESS_CASES:
                # B-10: «дай доступ к Miro» is the bot's own topic; the access rules win over a refusal.
                return fallback
            return result
        except Exception:
            # Operational failures and malformed model output never expose prompts/errors.
            # CancelledError is a BaseException and intentionally propagates.
            return fallback
