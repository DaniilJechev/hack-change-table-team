"""Thin aiogram adapter for the same application API as desktop."""

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from buddy.bot.session import context, private
from buddy.channels.repository import ChannelError
from buddy.channels.service import owner


async def profile(message, user, service, identity):
    found = await context(message, user, service, identity)
    if found is None:
        return None
    value = {"user_id": found.user_id, "team_id": found.team_id, "role": found.role}
    if found.demo:
        value.update(demo=True, session=found.session, owner="demo:" + str(found.session))
    await service.channels.repository.call(
        "put", user_id=owner(value), kind="state", id="telegram", payload={"chat_id": message.chat.id}
    )
    return value


async def device(message, agent_service, identity_client=None):
    if not private(message, message.from_user) or identity_client is None:
        await message.answer("Для входа нужен привязанный профиль в личном чате.")
        return
    if agent_service.channels.api.demo:
        await message.answer("Сервер в демо-режиме. В приложении выберите синтетическое демо.")
        return
    args = (message.text or "").split()
    if len(args) != 2 or len(args[1]) != 10 or not args[1].isalnum():
        await message.answer("Введите /desktop КОД из приложения AvitoCode. Подтверждайте только свой вход.")
        return
    await message.answer(
        "Подтвердить вход в Windows-приложение AvitoCode? Только если вы сами видите этот код в приложении: "
        + args[1].upper(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Подтвердить вход", callback_data="desktop:" + args[1].upper())]
            ]
        ),
    )


async def approve(query, agent_service, identity_client=None):
    if query.message is None or not private(query.message, query.from_user) or identity_client is None:
        await query.answer("Вход недоступен", show_alert=True)
        return
    try:
        agent_service.channels.api.limit(("approve", query.from_user.id), 10)
        p = await identity_client.profile(query.from_user.id)
        await agent_service.channels.repository.call(
            "approve", user_id=p.user_id, code=query.data.split(":", 1)[1]
        )
        await agent_service.channels.repository.call(
            "put", user_id=p.user_id, kind="state", id="telegram", payload={"chat_id": query.message.chat.id}
        )
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Вход подтверждён")
    except Exception:
        await query.answer("Код истёк или вход недоступен", show_alert=True)


async def text(message, agent_service, identity_client=None):
    p = await profile(message, message.from_user, agent_service, identity_client)
    if p:
        await agent_service.channels.submit(
            p,
            "tg:" + str(message.chat.id) + ":" + str(message.message_id),
            {"text": message.text},
            "telegram",
        )


async def callback(query, agent_service, identity_client=None):
    if query.message is None:
        return
    p = await profile(query.message, query.from_user, agent_service, identity_client)
    if not p:
        return
    try:
        state = await agent_service.channels.repository.call(
            "get", user_id=owner(p), kind="state", id="tgmessage:" + str(query.message.message_id)
        )
        await agent_service.channels.submit(
            p,
            "tg-action:" + query.id,
            {
                "type": "action",
                "message_id": state["message_id"],
                "data": query.data,
                "telegram_message_id": query.message.message_id,
            },
            "telegram",
        )
        await query.answer()
    except ChannelError:
        await query.answer("Кнопка устарела. Повторите вопрос.", show_alert=True)


async def document(message, agent_service, identity_client=None):
    p = await profile(message, message.from_user, agent_service, identity_client)
    if not p:
        return
    from buddy.bot.equator import LimitedDeck

    if message.document.file_size and message.document.file_size > 10 << 20:
        await message.answer("Файл больше 10 МиБ.")
        return
    import asyncio

    try:
        with LimitedDeck() as stream:
            async with asyncio.timeout(20):
                await message.bot.download(message.document, destination=stream, timeout=20)
            attachment = await agent_service.channels.save_file(
                p, message.document.file_name or "file", stream.getvalue()
            )
        await agent_service.channels.submit(
            p,
            "tg:" + str(message.chat.id) + ":" + str(message.message_id),
            {"attachment": attachment},
            "telegram",
        )
    except Exception:
        await message.answer("Не удалось принять файл. Попробуйте позже.")


def register(router):
    router.message.register(device, Command("desktop"))
    router.callback_query.register(approve, F.data.startswith("desktop:"))
    router.callback_query.register(callback, F.data)
    router.message.register(document, F.document)
    router.message.register(text, F.text & ~F.text.regexp(r"^/(start|help|demo)(\s|$)"))
