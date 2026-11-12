"""Execution boundary: H1/H2 business executors (stage 3) and templates for not yet implemented cases."""

import re
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from buddy.executors.context import Context
from buddy.mcp_hub.errors import ToolDenied
from buddy.mcp_hub.policy import READ_TOOLS, permitted
from buddy.render import to_plain
from buddy.router.rules import MENU_CHOICES
from buddy.router.service import CASES
from buddy.schemas import Action, ExecutorResponse

TEAM_MENU = (
    ("team_q1", "Кто отвечает за область?"),
    ("team_q2", "К кому идти в ситуации?"),
    ("team_q3", "Наставник и тимлид"),
    ("team_q4", "Можно ли писать напрямую?"),
    ("team_q5", "Этап проекта"),
    ("team_q6", "Связь задачи с проектом"),
    ("team_q7", "Стек команды"),
    ("team_q8", "Где найти объект?"),
)
SCHEDULED = ("brief_render", "access_events")
# RT-03: the refusal text is chosen by code from the request; HR BP only for salary, personal, medical data.
PERSONAL = re.compile(
    r"зарплат|оклад|получать|преми|персональн|паспорт|снилс|\bинн\b|диагноз|болезн|больничн|медицин"
)
OUT_OF_SCOPE_HR = (
    "Я помогаю с онбордингом. Вопросы о зарплате и персональных данных — к HR BP; "
    "остальные вопросы можно обсудить с наставником. /help — возможности."
)
OUT_OF_SCOPE = (
    "Это вне моей темы: я помогаю с онбордингом — бриф, доступы, вопросы о команде и playbook. "
    "С остальным поможет наставник. /help — возможности."
)


class Gateway(Protocol):
    async def call(self, case: str, server: str, tool: str, arguments: dict, *, user_id: str) -> dict: ...


class BudgetExceeded(Exception):
    pass


class ToolScope:
    def __init__(self, case: str, context: Context, gateway: Gateway):
        self.case, self.context, self.gateway = case, context, gateway
        self.attempts = 0
        self.audit: list[dict] = []

    async def call(self, server: str, tool: str, arguments: dict):
        # No user-controlled arguments, names, IDs or source bodies are logged.
        outcome = "ok"
        try:
            if self.attempts >= 3:
                raise BudgetExceeded()
            self.attempts += 1
            if self.case == "interns_status" and self.context.role == "intern":
                raise ToolDenied("Mentor role required")
            permitted(self.case, server, tool)
            if any(key in arguments for key in ("user_id", "team_id", "role")):
                raise ToolDenied("Identity arguments are not accepted from executors")
            return await self.gateway.call(self.case, server, tool, arguments, user_id=self.context.user_id)
        except BudgetExceeded:
            outcome = "budget_exceeded"
            raise
        except ToolDenied:
            outcome = "tool_denied"
            raise
        except BaseException:
            outcome = "unavailable"
            raise
        finally:
            self.audit.append(
                {
                    "case": self.case if self.case in Registry.cases else "invalid",
                    "server": server if server in READ_TOOLS else "invalid",
                    "tool": tool if tool in READ_TOOLS.get(server, ()) else "denied",
                    "user_id": self.context.user_id
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.context.user_id)
                    else "invalid",
                    "outcome": outcome,
                    "attempt": self.attempts,
                }
            )


def template(case: str, params: dict | None = None, text: str = "") -> ExecutorResponse:
    if case == "help":
        text = "Могу помочь с брифом, доступами, вопросами о команде и playbook. /ask — типы вопросов."
        if (params or {}).get("menu") == "team":
            text = "Выберите один из восьми типов вопросов о команде:"
    elif case == "clarify" and (params or {}).get("options") == MENU_CHOICES:
        # RT-03: an unclear text without access or a person — the team question types and /help.
        return ExecutorResponse(
            result="Не понял вопрос. Выберите тип вопроса о команде или напишите /help — что я умею.",
            actions=[Action(label=title, data="case:" + name) for name, title in TEAM_MENU],
        )
    elif case == "clarify":
        text = "Уточните: вас интересует доступ к инструменту или человек, которому можно написать?"
    elif case == "out_of_scope":
        text = OUT_OF_SCOPE_HR if PERSONAL.search(text.casefold()) else OUT_OF_SCOPE
    elif case.startswith("team_q"):
        text = (
            "Тип вопроса распознан. Ответы по данным команды пока не подключены. /ask — выбрать тип вопроса."
        )
    elif case in {"access_status", "access_task_analysis", "interns_status", "access_events"}:
        text = "Запрос о доступах распознан. Проверки доступов пока не подключены."
    elif case in {"brief_now", "brief_render"}:
        text = "Запрос брифа распознан. Сбор и отправка брифа пока не подключены."
    else:
        text = "Вопрос по playbook распознан. Поиск по документам пока не подключён."
    return ExecutorResponse(result=text)


def checked_response(raw: str, case: str, sources: dict[str, str]) -> ExecutorResponse:
    """Validate a future executor's answer; URLs can only come from trusted source IDs."""
    try:
        result = ExecutorResponse.model_validate_json(raw)
        if (
            not result.result.strip()
            or len(result.result) > 3000
            or re.search(r"https?://|www\.", result.result)
        ):
            raise ValueError("Inline URLs are not allowed")
        for citation in result.citations:
            url = sources[citation.source_id]
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or not parsed.path.strip("/")
                or re.search(r"\s", url)
            ):
                raise ValueError("Invalid source link")
        if not result.citations and case not in {"help", "clarify", "out_of_scope"}:
            raise ValueError("A factual response requires sources")
        return result
    except (ValidationError, ValueError, KeyError, TypeError):
        return template(case)


def render(result: ExecutorResponse, sources: dict[str, str]) -> str:
    # Result was validated before rendering; an unknown citation fails closed.
    try:
        links = list(dict.fromkeys(sources[item.source_id] for item in result.citations))
    except KeyError:
        return "Не нашёл подтверждённый источник."
    return result.result + ("\nИсточники:\n" + "\n".join(links) if links else "")


class Registry:
    cases = frozenset((*CASES, *SCHEDULED))

    def __init__(self, *, llm=None, settings=None):
        # Imported lazily: business services depend on the context module, not on this registry.
        from buddy.access.service import AccessService
        from buddy.brief.service import BriefService
        from buddy.rag.index import default_index
        from buddy.rag.qa import PlaybookService
        from buddy.rag.search import PlaybookSearch, create_search
        from buddy.team.service import TeamService

        self.access = AccessService()
        self.team = TeamService(self.access)
        search = create_search(settings) if settings is not None else PlaybookSearch(default_index())
        self.playbook = PlaybookService(search, llm, self.team)
        self.brief = BriefService(llm, settings)

    async def execute(
        self, case: str, params: dict, context: Context, runtime=None, text: str = ""
    ) -> ExecutorResponse:
        case = case if case in self.cases else "out_of_scope"
        if case == "interns_status" and context.role == "intern":
            return ExecutorResponse(result="Сводка стажёров доступна только наставнику или тимлиду.")
        if case == "playbook_qa":
            if params.get("chunks"):
                # FR-H3-07: «подробнее» answers from the chunk IDs kept in memory, without a new search.
                chunks = [chunk for chunk in params["chunks"].split(",") if chunk]
                return await self.playbook.details(chunks, params.get("query") or text, context, runtime)
            return await self.playbook.answer(params.get("query") or text, context, runtime)
        if runtime is None:
            return template(case, params, text)
        if case == "brief_now" and params.get("brief_id"):
            # FR-H0-12: a follow-up about items of a stored brief (number, PR, task, source), from memory.
            return await self.brief.item(
                context,
                runtime,
                params["brief_id"],
                item=params.get("item", ""),
                pr=params.get("pr", ""),
                task=params.get("task", ""),
                source=params.get("source", ""),
            )
        if case in {"brief_now", "brief_render"}:
            return await self.brief.now_brief(
                context, runtime, source=params.get("source", ""), scheduled=case == "brief_render"
            )
        if case == "access_status":
            return await self.access.status(context, runtime)
        if case == "access_task_analysis":
            return await self.access.task(context, runtime, params.get("task", ""))
        if case == "interns_status":
            return await self.access.interns(context, runtime)
        if case == "access_events":
            return await self.access.monitor(context, runtime) or ExecutorResponse(
                result="Изменений в доступах нет."
            )
        if case.startswith("team_q"):
            return await self.team.answer(case, params, text, context, runtime)
        return template(case, params, text)

    async def run_tools(self, case: str, context: Context, gateway: Gateway, calls: list[dict]):
        scope = ToolScope(case, context, gateway)
        for call in calls:
            try:
                await scope.call(call["server"], call["tool"], call["arguments"])
            except Exception:
                break
        return template(case if case in self.cases else "out_of_scope"), scope.audit

    async def trigger(self, case: str, context: Context, runtime=None) -> ExecutorResponse:
        # Used by callbacks and the scheduler; no Router dependency.
        return await self.execute(case, {}, context, runtime)


def format_response(response: ExecutorResponse) -> str:
    """Links are appended by code; executors never put URLs into their text."""
    return to_plain(response.result, response.folded, response.links)
