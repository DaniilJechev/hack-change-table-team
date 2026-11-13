from functools import partial

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from buddy.bot.identity import IdentityClient, IdentityError
from buddy.executors.core import TEAM_MENU, Context
from buddy.render import to_html, to_plain
from buddy.schemas import Action, ExecutorResponse
from buddy.service import AgentService

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
)


def team_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data="case:" + case)] for case, title in TEAM_MENU
        ]
    )


def keyboard(actions: list[Action]) -> InlineKeyboardMarkup | None:
    rows = []
    ratings = {}
    for action in actions:
        if not (action.url or action.data):
            continue
        button = (
            InlineKeyboardButton(text=action.label, url=action.url)
            if action.url
            else InlineKeyboardButton(text=action.label, callback_data=action.data)
        )
        if not action.url and (action.data or "").startswith(("brief:positive:", "brief:negative:")):
            brief_id = action.data.split(":", 2)[2]
            if brief_id not in ratings:
                ratings[brief_id] = []
                rows.append(ratings[brief_id])
            ratings[brief_id].append(button)
            ratings[brief_id].sort(key=lambda item: item.callback_data.startswith("brief:negative:"))
        else:
            rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def hide_brief_feedback(message: Message, brief_id: str) -> None:
    if message.reply_markup is None:
        return
    removed = {f"brief:{kind}:{brief_id}" for kind in ("positive", "negative", "read")}
    rows = [
        [button for button in row if button.callback_data not in removed]
        for row in message.reply_markup.inline_keyboard
    ]
    rows = [row for row in rows if row]
    await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def response_menu(case: str, response: ExecutorResponse | None = None):
    if response is not None and response.actions:
        return keyboard(response.actions)
    if case == "clarify":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Доступ к инструменту", callback_data="case:access_status")],
                [InlineKeyboardButton(text="Кто это и можно ли ему писать", callback_data="case:team_q3")],
            ]
        )
    if case == "out_of_scope":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Возможности", callback_data="case:help")],
                [InlineKeyboardButton(text="Спросить наставника", callback_data="mentor:contact")],
            ]
        )
    return team_menu() if case == "help" else None


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


def prefix(profile: Context) -> str:
    return "Демо: синтетический сценарий.\n" if profile.demo else ""


async def rich(method, response: ExecutorResponse, head: str = "", **kwargs):
    """FR-UI-01: HTML with expandable quotes; plain text when Telegram rejects the markup."""
    try:
        html = to_html(response.result, response.folded, response.links, head)
        return await method(text=html, parse_mode="HTML", **kwargs)
    except TelegramBadRequest:
        text = to_plain(response.result, response.folded, response.links, head)
        return await method(text=text, parse_mode=None, **kwargs)


async def send(
    message: Message,
    profile: Context,
    response: ExecutorResponse,
    case: str = "",
    agent_service: AgentService | None = None,
):
    try:
        sent = await rich(
            message.answer,
            response,
            prefix(profile),
            reply_markup=response_menu(case, response),
            disable_web_page_preview=True,
        )
    except Exception:
        # FR-H0-01: a brief that was not delivered stays unsent, so its changes are collected again.
        if agent_service is not None and response.brief_id:
            await agent_service.brief_delivered(response, profile, False)
        raise
    if agent_service is not None and response.brief_id:
        await agent_service.brief_delivered(response, profile, True)
        agent_service.remember_brief(message.chat.id, sent.message_id, response.brief_id)


async def deliver(message: Message, agent_service: AgentService, profile: Context, notes) -> list[str]:
    """Send messages to other participants; returns names that could not be reached."""
    missed = []
    for user_id, note in notes:
        chat = agent_service.chat_for(profile, user_id)
        if chat is None:
            missed.append(user_id)
            continue
        await rich(
            partial(message.bot.send_message, chat),
            note,
            prefix(profile),
            reply_markup=keyboard(note.actions),
            disable_web_page_preview=True,
        )
    return missed


async def demo(message: Message, agent_service: AgentService):
    if not private(message, message.from_user):
        await message.answer("Для демо откройте личный чат.")
        return
    arguments = (message.text or "").split(maxsplit=1)
    action = arguments[1].strip() if len(arguments) == 2 else ""
    user = message.from_user.id
    if action in {"reset", "off", "mentor", "intern"} or (not action and not agent_service.demo.active(user)):
        agent_service.clear_demo(user)
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
    else:
        result = await agent_service.trigger(value, profile)
        await send(message, profile, result, value, agent_service)


def register(router: Router):
    from buddy.bot.teamcard import document, teamcard

    router.message.register(teamcard, Command("teamcard"))
    router.message.register(document, F.document)
    router.message.register(demo, Command("demo"))
    router.message.register(ask, Command("ask"))
    router.message.register(stats, Command("stats"))
    router.message.register(settings, Command("settings"))
    router.message.register(reset, Command("reset"))
    router.message.register(route_text, F.text)
    router.callback_query.register(
        choose, F.data.func(lambda data: bool(data) and data.startswith(CALLBACKS))
    )
