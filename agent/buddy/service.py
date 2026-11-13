import os

from buddy.agent import Agent
from buddy.demo.controller import DemoController
from buddy.equator.service import EquatorService, local_pptx_gateway
from buddy.executors.core import Context, Registry
from buddy.graph import build_graph
from buddy.llm.client import CompatibleLLM
from buddy.memory import Memory
from buddy.memory.store import DEFAULT_MAX_TURNS, DEFAULT_TTL_SECONDS
from buddy.router.service import Router
from buddy.runtime import Runtime
from buddy.schemas import ExecutorResponse, RouteDecision


class AgentService:
    """Router → Executor plus H1/H2 interactive flows (marks, escalation, answers, FAQ, monitoring)."""

    def __init__(
        self,
        router: Router,
        *,
        demo_mode: bool,
        live: Runtime | None = None,
        settings=None,
        llm=None,
        memory: Memory | None = None,
    ):
        self.router = router
        self.executors = Registry(llm=llm, settings=settings)
        engine = getattr(settings, "agent_engine", None) or os.environ.get("AGENT_ENGINE", "graph")
        if engine not in {"legacy", "graph"}:
            raise ValueError("AGENT_ENGINE must be legacy or graph")
        self.engine = engine
        # FR-MEM-06: in process by default; app.py passes a Redis store for live mode.
        self.memory = memory or Memory(
            max_turns=getattr(settings, "memory_max_turns", DEFAULT_MAX_TURNS),
            ttl_seconds=getattr(settings, "memory_ttl_seconds", DEFAULT_TTL_SECONDS),
            enabled=getattr(settings, "memory_enabled", True),
        )
        # FR-AG-01: FakeLLM (LLM_PROVIDER=fake, demo without LLM_IN_DEMO) keeps the Router path.
        mode = getattr(settings, "agent_mode", "agent")
        agent = Agent(llm, self.executors) if mode == "agent" and isinstance(llm, CompatibleLLM) else None
        self.agent, self.llm = agent, llm
        self.graph = (
            build_graph(router, self.executors, self.memory, llm, agent) if engine == "graph" else None
        )
        self.brief_messages: dict[tuple[int, int], str] = {}
        self.demo = DemoController(demo_mode)
        self.live = live
        self.chats: dict[str, int] = {}
        self.known: dict[str, Context] = {}
        self.awaiting: dict[int, tuple[str, str, Context]] = {}
        self.awaiting_card: dict[int, Context] = {}
        self.equator = EquatorService(pptx_gateway=local_pptx_gateway() if demo_mode else None)

    async def equator_check(self, name: str, data: bytes, context: Context):
        """H4: rules-only deck check; returns the report and the stored check (or None)."""
        return await self.equator.check(name, data, context, self.runtime(context))

    async def equator_send(self, check_id: str, context: Context):
        return await self.equator.send(check_id, context, self.runtime(context))

    def runtime(self, context: Context) -> Runtime | None:
        if context.demo and context.session is not None and self.demo.active(context.session):
            return self.demo.runtime(context.session)
        return None if context.demo else self.live

    def remember(self, context: Context, chat_id: int) -> None:
        if not context.demo:
            self.chats[context.user_id] = chat_id
            self.known[context.user_id] = context

    def chat_for(self, context: Context, user_id: str) -> int | None:
        # Demo: every synthetic participant is shown in the presenter's chat.
        if context.demo:
            return context.session
        return self.chats.get(user_id)

    async def handle(self, text: str, context: Context):
        if self.graph is not None:
            state = await self.graph.ainvoke(
                {"text": text, "context": context, "runtime": self.runtime(context)}
            )
            return state["decision"], state["response"]
        decision = await self.router.route(text, context=context)
        response = await self.executors.execute(
            decision.case, decision.params, context, self.runtime(context), text
        )
        return decision, response

    async def trigger(self, case: str, context: Context):
        if self.graph is not None:
            # Scheduled cases (brief_render, access_events) are not Router cases: skip schema validation.
            decision = RouteDecision.model_construct(case=case, params={})
            state = await self.graph.ainvoke(
                {"context": context, "runtime": self.runtime(context), "decision": decision}
            )
            return state["response"]
        return await self.executors.trigger(case, context, self.runtime(context))

    async def mark(self, object_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Данные доступов сейчас недоступны.")
        return await self.executors.access.mark(context, runtime, object_id)

    async def request_access(self, object_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Данные доступов сейчас недоступны.")
        return await self.executors.access.request(context, runtime, object_id)

    async def reminder(self, object_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Данные доступов сейчас недоступны.")
        return await self.executors.access.reminder(context, runtime, object_id)

    async def escalate(self, kind: str, context: Context):
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Передача вопросов сейчас недоступна."), []
        return await self.executors.team.escalate(kind, context, runtime)

    async def reset(self, context: Context) -> ExecutorResponse:
        """FR-MEM-03: /reset forgets the dialog window of this user and team."""
        await self.memory.clear(context)
        return ExecutorResponse(result="Контекст диалога очищен. Следующий вопрос начнём с чистого листа.")

    def clear_demo(self, telegram_user: int) -> None:
        self.memory.clear_demo(telegram_user)
        self.awaiting.pop(telegram_user, None)
        self.awaiting_card.pop(telegram_user, None)
        self.equator.clear(telegram_user)
        for cache in (
            self.executors.access.previous,
            self.executors.team.pending,
            self.executors.team.deferred,
        ):
            for key in list(cache):
                if key.endswith(f":{telegram_user}"):
                    cache.pop(key, None)

    async def start_answer(self, question_id: str, context: Context, telegram_user: int) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Ответы сейчас недоступны.")
        try:
            record = await runtime.data.question(context.user_id, question_id)
        except Exception:
            return ExecutorResponse(result="Вопрос не найден.")
        answerer = record["assigned_to"] if context.demo else context.user_id
        if record["assigned_to"] != answerer:
            return ExecutorResponse(result="Ответить может только адресат вопроса.")
        self.awaiting[telegram_user] = (question_id, answerer, context)
        return ExecutorResponse(result="Напишите ответ одним сообщением — я передам его автору вопроса.")

    def is_answering(self, telegram_user: int) -> bool:
        return telegram_user in self.awaiting

    async def submit_answer(self, text: str, context: Context, telegram_user: int):
        question_id, answerer, original = self.awaiting.pop(telegram_user)
        if context != original:
            return [(context.user_id, ExecutorResponse(result="Контекст изменился. Откройте вопрос заново."))]
        runtime = self.runtime(context)
        if runtime is None:
            return [(answerer, ExecutorResponse(result="Ответы сейчас недоступны."))]
        return await self.executors.team.deliver_answer(answerer, question_id, text, runtime)

    async def publish(self, question_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="FAQ сейчас недоступен.")
        answerer = context.user_id
        if context.demo:
            try:
                answerer = (await runtime.data.question(context.user_id, question_id))["assigned_to"]
            except Exception:
                return ExecutorResponse(result="Вопрос не найден.")
        return await self.executors.team.publish(answerer, question_id, runtime)

    async def rate(self, value: str, helpful: bool, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Спасибо за оценку!")
        return await self.executors.team.rate(context.user_id, value, helpful, runtime)

    async def monitor(self, context: Context) -> ExecutorResponse | None:
        runtime = self.runtime(context)
        if runtime is None:
            return None
        try:
            if not (await runtime.data.flags(context.user_id)).get("access_monitoring", True):
                return None
        except Exception:
            return None
        return await self.executors.access.monitor(context, runtime)

    async def monitor_all(self) -> list[tuple[int, ExecutorResponse]]:
        """Scheduled access_events for users seen by this process (live mode)."""
        result = []
        for user_id, context in list(self.known.items()):
            response = await self.monitor(context)
            if response is not None and user_id in self.chats:
                result.append((self.chats[user_id], response))
        return result

    async def upload_card(self, source: bytes, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if context.role not in {"mentor", "teamlead"}:
            return ExecutorResponse(result="Загрузка карточки доступна только наставнику или тимлиду.")
        if runtime is None:
            return ExecutorResponse(result="Загрузка карточки сейчас недоступна.")
        try:
            card = await runtime.data.upload_team_card(context.user_id, source)
        except Exception as error:
            fields = getattr(error, "fields", ())
            return ExecutorResponse(
                result="Карточка не загружена."
                + ("\n" + "\n".join(fields) if fields else " Проверьте поля и принадлежность своей команде.")
            )
        return ExecutorResponse(result=f"Карточка команды загружена. Версия: {card['version']}.")

    async def brief_delivered(self, response: ExecutorResponse, context: Context, ok: bool) -> None:
        runtime = self.runtime(context)
        if response.brief_id and runtime is not None:
            await self.executors.brief.delivered(response.brief_id, runtime, ok)

    def remember_brief(self, chat_id: int, message_id: int, brief_id: str) -> None:
        self.brief_messages[(chat_id, message_id)] = brief_id
        while len(self.brief_messages) > 1024:
            self.brief_messages.pop(next(iter(self.brief_messages)))

    async def brief_signal(self, kind: str, brief_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Брифы сейчас недоступны.")
        return await self.executors.brief.signal(kind, brief_id, context, runtime)

    async def brief_reply(self, chat_id: int, message_id: int, context: Context) -> None:
        brief_id = self.brief_messages.get((chat_id, message_id))
        runtime = self.runtime(context)
        if brief_id and runtime is not None:
            await self.executors.brief.replied(brief_id, context, runtime)

    async def stats(self, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Статистика сейчас недоступна.")
        return await self.executors.brief.stats(context, runtime)

    async def settings(self, context: Context, arguments: str) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Настройки сейчас недоступны.")
        return await self.executors.brief.settings(context, runtime, arguments)

    async def briefs_due(self) -> list[tuple[int, ExecutorResponse, Context]]:
        """Scheduler tick (live mode): morning briefs that are due for users seen by this process."""
        result = []
        for user_id, context in list(self.known.items()):
            runtime = self.runtime(context)
            if runtime is None or user_id not in self.chats:
                continue
            response = await self.executors.brief.scheduled(context, runtime)
            if response is not None:
                result.append((self.chats[user_id], response, context))
        return result
