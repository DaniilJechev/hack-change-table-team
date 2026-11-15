from aiogram.types import CallbackQuery, Message

from buddy.bot.identity import IdentityClient
from buddy.bot.rendering import deliver, hide_brief_feedback, send
from buddy.bot.session import context
from buddy.orchestration.service import AgentService

CALLBACKS = (
    "case:",
    "mentor:",
    "request:",
    "mark:",
    "remind:",
    "esc:",
    "ans:",
    "faq:",
    "nofaq:",
    "rate:",
    "brief:",
    "equator:",
)


async def choose(
    query: CallbackQuery, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    await query.answer()
    if not isinstance(query.message, Message):
        return
    message = query.message
    profile = await context(message, query.from_user, agent_service, identity_client)
    if profile is None:
        return
    data = query.data or ""
    kind, _, value = data.partition(":")
    if kind == "mentor":
        await message.answer(
            "Используйте «Спросить наставника» под ответом бота — вопрос уйдёт наставнику с контекстом."
        )
        return
    if kind == "mark":
        await send(message, profile, await agent_service.mark(value, profile))
    elif kind == "request":
        await send(message, profile, await agent_service.request_access(value, profile))
    elif kind == "remind":
        await send(message, profile, await agent_service.reminder(value, profile))
    elif kind == "esc":
        reply, notes = await agent_service.escalate(value, profile)
        missed = await deliver(message, agent_service, profile, notes)
        if missed:
            reply.result += (
                "\nАдресат пока не открывал бота — вопрос сохранён. "
                "Автоматическая доставка пока не настроена."
            )
        await send(message, profile, reply)
    elif kind == "ans":
        await send(message, profile, await agent_service.start_answer(value, profile, query.from_user.id))
    elif kind == "faq":
        await send(message, profile, await agent_service.publish(value, profile))
    elif kind == "nofaq":
        await message.answer("Хорошо, в FAQ не сохраняю.")
    elif kind == "brief":
        action, _, brief_id = value.partition(":")
        response = await agent_service.brief_signal(action, brief_id, profile)
        if action in {"positive", "negative"} and response.feedback_saved:
            await hide_brief_feedback(message, brief_id)
        await send(message, profile, response)
    elif kind == "rate":
        target, _, mark = value.rpartition(":")
        await send(message, profile, await agent_service.rate(target, mark == "1", profile))
    elif kind == "equator":
        from buddy.bot.equator import to_mentor

        await to_mentor(message, agent_service, profile, value)
    else:
        result = await agent_service.trigger(value, profile)
        await send(message, profile, result, value, agent_service)
