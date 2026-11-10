from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from buddy.bot.identity import IdentityClient, IdentityError
from buddy.executors.core import TEAM_MENU, Context
from buddy.service import AgentService


def team_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data="case:" + case)] for case, title in TEAM_MENU
        ]
    )


def response_menu(case: str):
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
            return Context(profile.user_id, profile.team_id, profile.role)
        except IdentityError:
            pass
    await message.answer(
        "Нужна привязка через /start с персональным кодом. "
        "Для публичного синтетического примера включите /demo."
    )
    return None


async def demo(message: Message, agent_service: AgentService):
    if not private(message, message.from_user):
        await message.answer("Для демо откройте личный чат.")
        return
    arguments = (message.text or "").split(maxsplit=1)
    action = arguments[1].strip() if len(arguments) == 2 else ""
    await message.answer(agent_service.demo.command(message.from_user.id, action), parse_mode=None)


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
    if agent_service.router.llm is not None:
        await message.bot.send_chat_action(message.chat.id, "typing")
    decision, response = await agent_service.handle(message.text or "", profile)
    await message.answer(
        ("Демо: синтетический сценарий.\n" if profile.demo else "") + response.result,
        reply_markup=response_menu(decision.case),
        parse_mode=None,
    )


async def choose(
    query: CallbackQuery, agent_service: AgentService, identity_client: IdentityClient | None = None
):
    await query.answer()
    if not isinstance(query.message, Message):
        return
    profile = await context(query.message, query.from_user, agent_service, identity_client)
    if profile is None:
        return
    if query.data == "mentor:contact":
        await query.message.answer(
            "Свяжитесь с вашим наставником самостоятельно. Передача вопросов через бота пока не подключена."
        )
        return
    result = await agent_service.trigger((query.data or "").removeprefix("case:"), profile)
    await query.message.answer(
        ("Демо: синтетический сценарий.\n" if profile.demo else "") + result.result, parse_mode=None
    )


def register(router: Router):
    router.message.register(demo, Command("demo"))
    router.message.register(ask, Command("ask"))
    router.message.register(route_text, F.text)
    router.callback_query.register(choose, F.data.startswith("case:") | (F.data == "mentor:contact"))
