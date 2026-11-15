from aiogram.types import Message

from buddy.bot.identity import IdentityClient
from buddy.bot.rendering import send, team_menu
from buddy.bot.session import context, private
from buddy.orchestration.service import AgentService


async def demo(message: Message, agent_service: AgentService):
    if not private(message, message.from_user):
        await message.answer("Для демо откройте личный чат.")
        return
    arguments = (message.text or "").split(maxsplit=1)
    action = arguments[1].strip() if len(arguments) == 2 else ""
    user = message.from_user.id
    if action in {"reset", "off", "mentor", "intern"} or (not action and not agent_service.demo.active(user)):
        agent_service.clear_demo(user, clear_equator=action == "reset")
    before = None
    if action == "next" and agent_service.demo.active(user):
        # Baseline at the current step, so the next step reports only real changes (access_events).
        before = agent_service.demo.context(user)
        await agent_service.monitor(before)
    text = agent_service.demo.command(user, action)
    await message.answer(text, parse_mode=None)
    if before is not None:
        change = await agent_service.monitor(agent_service.demo.context(user))
        if change is not None:
            await send(message, before, change)


async def ask(message: Message):
    if not private(message, message.from_user):
        await message.answer("Для вопросов о команде откройте личный чат.")
        return
    await message.answer("Выберите один из восьми типов вопросов о команде:", reply_markup=team_menu())


async def stats(message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is not None:
        await send(message, profile, await agent_service.stats(profile))


async def reset(message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is not None:
        await send(message, profile, await agent_service.reset(profile))


async def settings(
    message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is not None:
        arguments = (message.text or "").split(maxsplit=1)
        value = arguments[1] if len(arguments) == 2 else ""
        await send(message, profile, await agent_service.settings(profile, value))
