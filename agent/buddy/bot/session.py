from aiogram.types import Message, User

from buddy.bot.identity import IdentityClient, IdentityError
from buddy.contracts import Context
from buddy.orchestration.service import AgentService


def private(message: Message, user: User | None) -> bool:
    return (
        message.chat.type == "private"
        and user is not None
        and not user.is_bot
        and user.id > 0
        and message.chat.id == user.id
    )


async def context(
    message: Message, user: User | None, agent_service: AgentService, identity_client: IdentityClient | None
) -> Context | None:
    if not private(message, user):
        await message.answer("Для работы с ботом откройте личный чат.")
        return None
    if agent_service.demo.active(user.id):
        return agent_service.demo.context(user.id)
    if identity_client is not None:
        try:
            profile = await identity_client.profile(user.id)
            found = Context(profile.user_id, profile.team_id, profile.role)
            agent_service.remember(found, message.chat.id)
            return found
        except IdentityError:
            pass
    await message.answer(
        "Нужна привязка через /start с персональным кодом. "
        "Для публичного синтетического примера включите /demo."
    )
    return None
