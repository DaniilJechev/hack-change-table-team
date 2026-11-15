from aiogram.types import Message

from buddy.bot.identity import IdentityClient
from buddy.bot.rendering import deliver, send
from buddy.bot.session import context
from buddy.orchestration.service import AgentService


async def route_text(
    message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is None:
        return
    expected_card = agent_service.awaiting_card.pop(message.from_user.id, None)
    if expected_card is not None:
        if profile != expected_card:
            await message.answer("Профиль изменился. Повторите /teamcard.")
        else:
            result = await agent_service.upload_card((message.text or "").encode("utf-8"), profile)
            await message.answer(result.result, parse_mode=None)
        return
    if agent_service.is_answering(message.from_user.id):
        notes = await agent_service.submit_answer(message.text or "", profile, message.from_user.id)
        missed = await deliver(message, agent_service, profile, notes)
        if missed:
            await message.answer(
                "Автор вопроса пока не открывал бота — ответ сохранён. "
                "Автоматическая доставка пока не настроена."
            )
        return
    if message.reply_to_message is not None:
        # FR-H0-08: a reply to the brief message is a read signal.
        await agent_service.brief_reply(message.chat.id, message.reply_to_message.message_id, profile)
    if agent_service.router.llm is not None:
        await message.bot.send_chat_action(message.chat.id, "typing")
    decision, response = await agent_service.handle(message.text or "", profile)
    await send(message, profile, response, decision.case, agent_service)
