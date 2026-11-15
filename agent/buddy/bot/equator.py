"""Telegram H4 upload and independently tracked file/report delivery."""

import asyncio
from functools import partial
from io import BytesIO

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from buddy.bot.rendering import keyboard, prefix, send
from buddy.bot.waiting import typing
from buddy.contracts import Action, Context
from buddy.data.contracts import DataError
from buddy.equator.slides import MAX_BYTES
from buddy.orchestration.service import AgentService
from buddy.render import _inline, cite


class LimitedDeck(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise ValueError("Upload too large")
        return super().write(data)


class PartialReportError(Exception):
    """At least one report chunk was delivered before another chunk failed."""


async def report(method, response, head="", **kwargs):
    """Keep every remark and its source; split on lines before Telegram's limit."""
    text, folded, links = cite(response.result, response.folded, response.links)
    text = head + "\n".join([text, *folded])
    chunks, chunk = [], ""
    for line in text.splitlines():
        # Preserve very long untrusted lines as text chunks too.
        pieces = [line[i : i + 1800] for i in range(0, len(line), 1800)] or [""]
        for piece in pieces:
            if len(chunk) + len(piece) > 2200:
                chunks.append(chunk)
                chunk = ""
            chunk += ("\n" if chunk else "") + piece
    if chunk:
        chunks.append(chunk)
    for index, chunk in enumerate(chunks):
        options = dict(kwargs)
        if index != len(chunks) - 1:
            options.pop("reply_markup", None)
        html = "\n".join(_inline(line, links) for line in chunk.splitlines())
        try:
            await method(text=html, parse_mode="HTML", **options)
        except TelegramForbiddenError as error:
            if index:
                raise PartialReportError() from error
            raise
        except TelegramBadRequest:
            # Only the referenced URLs accompany the fallback chunk.
            import re

            used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", chunk) if 1 <= int(n) <= len(links)})
            sources = "".join(f"\n[{n}] {links[n - 1]}" for n in used)
            # URLs may be long: send separately, never truncate remarks.
            try:
                await method(text=chunk, parse_mode=None, **options)
            except Exception as error:
                if index:
                    raise PartialReportError() from error
                raise
            if sources:
                for start in range(0, len(sources), 3500):
                    await method(text=sources[start : start + 3500], parse_mode=None)


async def deck(message: Message, agent_service: AgentService, profile: Context) -> None:
    await message.answer("Презентация получена. Подготавливаю проверку…")
    async with typing(message):
        await _check_deck(message, agent_service, profile)


async def _check_deck(message: Message, agent_service: AgentService, profile: Context) -> None:
    document = message.document
    if (document.file_name or "").lower().endswith((".pdf", ".pptx")):
        runtime = agent_service.runtime(profile)
        if runtime is not None:
            try:
                await agent_service.equator.invalidate(profile, runtime)
            except (DataError, OSError):
                await message.answer("История проверок недоступна. Попробуйте позже.")
                return
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
    response, record = await agent_service.equator_check(
        document.file_name or "", data, profile, file_id=document.file_id
    )
    await report(
        message.answer,
        response,
        prefix(profile),
        reply_markup=keyboard(response.actions),
        disable_web_page_preview=True,
    )


async def to_mentor(message: Message, agent_service: AgentService, profile: Context, check_id: str) -> None:
    retry_unknown = check_id.startswith("retry:")
    check_id = check_id.removeprefix("retry:")
    service = agent_service.equator
    lock = service.locks.setdefault(service.owner(profile), asyncio.Lock())
    async with lock:
        reply, mentor_id, note = await agent_service.equator_send(check_id, profile)
        if note is None:
            await send(message, profile, reply)
            return
        record = service.find(check_id, profile)
        runtime = agent_service.runtime(profile)
        chat = agent_service.chat_for(profile, mentor_id) if mentor_id else None
        if chat is None and not profile.demo and runtime is not None:
            try:
                chat = await runtime.data.mentor_chat(profile.user_id)
            except DataError:
                pass
        if chat is None:
            reply.result = (
                "Наставник пока недоступен: не доставлено. "
                "Можно повторить отправку после привязки наставника.\n" + reply.result
            )
        elif record is not None:
            for part in ("file", "report"):
                state = getattr(record, "delivery_" + part)
                if state == "sent":
                    continue
                if state == "sending":
                    try:
                        await service.delivery(record, part, "unknown", profile, runtime)
                    except (DataError, OSError):
                        pass
                    state = "unknown"
                if state == "unknown" and not retry_unknown:
                    reply.result = (
                        "Исход доставки неизвестен. Повтор может создать копию у наставника.\n" + reply.result
                    )
                    reply.actions = [
                        Action(label="Повторить, возможна копия", data="equator:retry:" + check_id)
                    ]
                    await send(message, profile, reply)
                    return
                try:
                    await service.claim(record, part, profile, runtime, retry_unknown=retry_unknown)
                except DataError:
                    reply.result = (
                        "Доставка уже обрабатывается или история недоступна. Повторите позже.\n"
                        + reply.result
                    )
                    await send(message, profile, reply)
                    return
                state = "sent"
                try:
                    if part == "file":
                        if not record.file_id:
                            raise ValueError("Missing Telegram file")
                        await message.bot.send_document(
                            chat, record.file_id, caption="Презентация к экватору"
                        )
                    else:
                        await report(
                            partial(message.bot.send_message, chat),
                            note,
                            prefix(profile),
                            disable_web_page_preview=True,
                        )
                except (TelegramBadRequest, TelegramForbiddenError, ValueError):
                    state = "failed"
                except Exception:
                    state = "unknown"
                try:
                    await service.delivery(record, part, state, profile, runtime)
                except (DataError, OSError):
                    state = "unknown"
                if state != "sent":
                    reply.result = (
                        "Не удалось доставить "
                        + ("файл" if part == "file" else "отчёт")
                        + (
                            ". Можно повторить отправку."
                            if state == "failed"
                            else ". Исход неизвестен; повтор может создать копию."
                        )
                        + "\n"
                        + reply.result
                    )
                    if state == "unknown":
                        reply.actions = [
                            Action(label="Повторить, возможна копия", data="equator:retry:" + check_id)
                        ]
                    await send(message, profile, reply)
                    return
            reply.result = "Отправлено наставнику вместе с отчётом проверки.\n" + reply.result
        await send(message, profile, reply)
