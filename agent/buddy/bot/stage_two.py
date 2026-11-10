from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from buddy.bot.identity import IdentityClient, IdentityError
from buddy.executors.core import TEAM_MENU, Context, format_response
from buddy.schemas import Action, ExecutorResponse
from buddy.service import AgentService

CALLBACKS = ("case:", "mentor:", "mark:", "remind:", "esc:", "ans:", "faq:", "nofaq:", "rate:")


def team_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data="case:" + case)] for case, title in TEAM_MENU
        ]
    )


def keyboard(actions: list[Action]) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(text=action.label, url=action.url)
            if action.url
            else InlineKeyboardButton(text=action.label, callback_data=action.data)
        ]
        for action in actions
        if action.url or action.data
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


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


async def send(message: Message, profile: Context, response: ExecutorResponse, case: str = ""):
    await message.answer(
        prefix(profile) + format_response(response),
        reply_markup=response_menu(case, response),
        parse_mode=None,
        disable_web_page_preview=True,
    )


async def deliver(message: Message, agent_service: AgentService, profile: Context, notes) -> list[str]:
    """Send messages to other participants; returns names that could not be reached."""
    missed = []
    for user_id, note in notes:
        chat = agent_service.chat_for(profile, user_id)
        if chat is None:
            missed.append(user_id)
            continue
        await message.bot.send_message(
            chat,
            prefix(profile) + format_response(note),
            reply_markup=keyboard(note.actions),
            parse_mode=None,
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
    if agent_service.router.llm is not None:
        await message.bot.send_chat_action(message.chat.id, "typing")
    decision, response = await agent_service.handle(message.text or "", profile)
    await send(message, profile, response, decision.case)


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
    elif kind == "rate":
        target, _, mark = value.rpartition(":")
        await send(message, profile, await agent_service.rate(target, mark == "1", profile))
    else:
        result = await agent_service.trigger(value, profile)
        await send(message, profile, result, value)


def register(router: Router):
    from buddy.bot.teamcard import document, teamcard

    router.message.register(teamcard, Command("teamcard"))
    router.message.register(document, F.document)
    router.message.register(demo, Command("demo"))
    router.message.register(ask, Command("ask"))
    router.message.register(route_text, F.text)
    router.callback_query.register(
        choose, F.data.func(lambda data: bool(data) and data.startswith(CALLBACKS))
    )
