from buddy.demo.controller import DemoController
from buddy.executors.core import Context, Registry
from buddy.router.service import Router
from buddy.runtime import Runtime
from buddy.schemas import ExecutorResponse


class AgentService:
    """Router → Executor plus H1/H2 interactive flows (marks, escalation, answers, FAQ, monitoring)."""

    def __init__(self, router: Router, *, demo_mode: bool, live: Runtime | None = None):
        self.router = router
        self.executors = Registry()
        self.demo = DemoController(demo_mode)
        self.live = live
        self.chats: dict[str, int] = {}
        self.known: dict[str, Context] = {}
        self.awaiting: dict[int, tuple[str, str, Context]] = {}

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
        decision = await self.router.route(text, context=context)
        response = await self.executors.execute(
            decision.case, decision.params, context, self.runtime(context), text
        )
        return decision, response

    async def trigger(self, case: str, context: Context):
        return await self.executors.trigger(case, context, self.runtime(context))

    async def mark(self, object_id: str, context: Context) -> ExecutorResponse:
        runtime = self.runtime(context)
        if runtime is None:
            return ExecutorResponse(result="Данные доступов сейчас недоступны.")
        return await self.executors.access.mark(context, runtime, object_id)

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

    def clear_demo(self, telegram_user: int) -> None:
        key = f"demo-daniil:{telegram_user}"
        self.awaiting.pop(telegram_user, None)
        self.executors.access.previous.pop(key, None)
        self.executors.team.pending.pop(key, None)
        self.executors.team.deferred.pop(key, None)

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
        return await self.executors.access.monitor(context, runtime)

    async def monitor_all(self) -> list[tuple[int, ExecutorResponse]]:
        """Scheduled access_events for users seen by this process (live mode)."""
        result = []
        for user_id, context in list(self.known.items()):
            response = await self.monitor(context)
            if response is not None and user_id in self.chats:
                result.append((self.chats[user_id], response))
        return result
