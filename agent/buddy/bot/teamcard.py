"""Private mentor-only YAML upload; never routes attachments through an LLM."""

import asyncio
from io import BytesIO

from aiogram.types import Message

from buddy.bot.identity import IdentityClient
from buddy.bot.stage_two import context
from buddy.service import AgentService

MAX_BYTES = 1 << 20


class LimitedUpload(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise ValueError("Upload too large")
        return super().write(data)


async def teamcard(
    message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is None:
        return
    if profile.role not in {"mentor", "teamlead"}:
        await message.answer("Загрузка карточки доступна только наставнику или тимлиду.")
        return
    agent_service.awaiting_card[message.from_user.id] = profile
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        agent_service.awaiting_card.pop(message.from_user.id, None)
        result = await agent_service.upload_card(parts[1].encode("utf-8"), profile)
        await message.answer(result.result, parse_mode=None)
    else:
        await message.answer("Пришлите YAML-файл или YAML-текст карточки своей команды (до 1 МиБ).")


async def document(
    message: Message, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    profile = await context(message, message.from_user, agent_service, identity_client)
    if profile is None:
        return
    expected = agent_service.awaiting_card.pop(message.from_user.id, None)
    name = (message.document.file_name or "").lower()
    if expected is None and not name.endswith((".yaml", ".yml")):
        # H4: any other file outside /teamcard is a deck for the equator check (PDF/PPTX).
        from buddy.bot.equator import deck

        await deck(message, agent_service, profile)
        return
    if expected is None or profile != expected:
        await message.answer("Сначала отправьте /teamcard от наставника или тимлида.")
        return
    if message.document.file_size and message.document.file_size > MAX_BYTES:
        await message.answer("Файл больше 1 МиБ. Загрузите меньшую карточку через /teamcard.")
        return
    try:
        with LimitedUpload() as stream:
            async with asyncio.timeout(10):
                await message.bot.download(message.document, destination=stream, timeout=10)
            source = stream.getvalue()
    except Exception:
        await message.answer("Не удалось прочитать YAML-файл. Повторите /teamcard.")
        return
    result = await agent_service.upload_card(source, profile)
    await message.answer(result.result, parse_mode=None)
