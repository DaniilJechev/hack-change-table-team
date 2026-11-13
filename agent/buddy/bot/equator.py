"""H4 in Telegram: a PDF/PPTX deck → rules-only report; [Отправить наставнику] → tracker mark.

The file is only read: it is never edited, and it reaches the mentor as the same Telegram file.
"""

import asyncio
from functools import partial
from io import BytesIO

from aiogram.types import Message

from buddy.bot.stage_two import keyboard, prefix, rich, send
from buddy.equator.slides import MAX_BYTES
from buddy.executors.context import Context
from buddy.schemas import ExecutorResponse
from buddy.service import AgentService


class LimitedDeck(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise ValueError("Upload too large")
        return super().write(data)


async def deck(message: Message, agent_service: AgentService, profile: Context) -> None:
    document = message.document
    if document.file_size and document.file_size > MAX_BYTES:
        await message.answer("Файл больше 10 МиБ. Сожмите презентацию или пришлите PDF.")
        return
    try:
        with LimitedDeck() as stream:
            async with asyncio.timeout(20):
                await message.bot.download(document, destination=stream, timeout=20)
            data = stream.getvalue()
    except Exception:
        await message.answer("Не удалось скачать файл. Пришлите презентацию ещё раз.")
        return
    await message.answer("Проверяю презентацию: структура и задачи в Jira…")
    response, record = await agent_service.equator_check(document.file_name or "", data, profile)
    if record is not None:
        record.file_id = document.file_id
    await send(message, profile, response)


async def to_mentor(message: Message, agent_service: AgentService, profile: Context, check_id: str) -> None:
    reply, mentor_id, note = await agent_service.equator_send(check_id, profile)
    if note is None:
        await send(message, profile, reply)
        return
    record = agent_service.equator.find(check_id, profile)
    chat = agent_service.chat_for(profile, mentor_id) if mentor_id else None
    if chat is None:
        reply.result = "Наставник пока не открывал бота: файл не доставлен, отчёт сохранён.\n" + reply.result
    else:
        if record is not None and record.file_id:
            await message.bot.send_document(chat, record.file_id, caption="Презентация к экватору")
        await rich(
            partial(message.bot.send_message, chat),
            note,
            prefix(profile),
            reply_markup=keyboard(note.actions),
            disable_web_page_preview=True,
        )
        reply.result = "Отправлено наставнику вместе с отчётом проверки.\n" + reply.result
    await send(message, profile, ExecutorResponse(result=reply.result))
