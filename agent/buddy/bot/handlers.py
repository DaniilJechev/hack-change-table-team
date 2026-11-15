from aiogram import Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from buddy.bot.identity import IdentityClient, IdentityError
from buddy.bot.registration import register
from buddy.orchestration.service import AgentService

WELCOME = (
    "Привет! Я AI-buddy — помощник стажёра.\n"
    "Сейчас работает каркас: /start и /help.\n"
    "Привязка аккаунта, бриф, доступы, ответы о команде и playbook появятся на следующих этапах. "
    "Данные команды пока недоступны."
)
HELP = (
    "Доступно сейчас: /start — приветствие, /help — помощь.\n"
    "Это каркас Этапа 0. Коды приглашения пока не активируются. "
    "Интеграции H0–H3 ещё не подключены."
)


def menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/help")]], resize_keyboard=True)


async def start(
    message: Message, identity_client: IdentityClient | None = None, agent_service: AgentService | None = None
) -> None:
    if identity_client is None:
        await message.answer(
            WELCOME
            if agent_service is None
            else "Привет! Я AI-buddy. /demo — синтетический сценарий. Вопросы о команде "
                 "пишите обычным текстом. "
            "Для реального профиля нужен настроенный backend и персональный код приглашения.",
            reply_markup=menu(),
        )
        return
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.is_bot
        or message.chat.id != message.from_user.id
    ):
        await message.answer("Для привязки аккаунта откройте личный чат с ботом.")
        return
    code = (message.text or "").split(maxsplit=1)
    try:
        if len(code) == 2:
            profile = await identity_client.bind(code[1].strip(), message.from_user.id, message.chat.id)
        else:
            profile = await identity_client.profile(message.from_user.id)
        roles = {"intern": "стажёр", "mentor": "наставник", "teamlead": "тимлид"}
        await message.answer(
            f"Аккаунт подключён. Роль: {roles[profile.role]}.\n"
            f"Команда: {profile.team_id}.\n"
            "Напишите вопрос о команде, доступах или playbook. /brief — бриф, /help — помощь.",
            reply_markup=menu(),
            parse_mode=None,
        )
    except IdentityError as exc:
        texts = {
            "unbound": "Попросите у наставника персональную ссылку приглашения и откройте её в личном чате.",
            "invalid": "Код приглашения недействителен или истёк. Попросите у наставника новую ссылку.",
            "conflict": "Этот аккаунт уже связан с другим профилем. Обратитесь к наставнику.",
        }
        await message.answer(texts.get(exc.kind, "Сервис привязки временно недоступен. Попробуйте позже."))


async def help_command(
    message: Message, identity_client: IdentityClient | None = None, agent_service: AgentService | None = None
) -> None:
    text = (
        HELP
        if identity_client is None
        else (
            "Этап 1: /start с персональным кодом — привязка, /start без кода — профиль, /help — помощь. "
            "Привязка доступна только в личном чате. Бриф и H0–H3 ещё не подключены."
        )
    )
    if agent_service is not None:
        text = (
            "/start — профиль, /demo — синтетический сценарий, "
            "/demo next — следующий шаг, /demo reset — сначала, /demo off — выход. "
            "/brief — бриф изменений, /stats — статистика брифов, /settings — время и тихий режим, "
            "/reset — забыть контекст диалога. "
            "/access — доступы, /teamcard — карточка для наставника/тимлида. "
            "Вопросы о команде и о процессах из AvitoTech Playbook можно писать обычным текстом."
        )
    await message.answer(text, reply_markup=menu())


def create_dispatcher(
    identity_client: IdentityClient | None = None, agent_service: AgentService | None = None
) -> Dispatcher:
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    dispatcher = Dispatcher()
    dispatcher["identity_client"] = identity_client
    dispatcher["agent_service"] = agent_service
    if agent_service is not None:
        if getattr(agent_service, "channels", None) is not None:
            from buddy.channels.telegram import register as register_channels

            register_channels(router)
        register(router)
    dispatcher.include_router(router)
    return dispatcher
