from aiogram import Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

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


async def start(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=menu())


async def help_command(message: Message) -> None:
    await message.answer(HELP, reply_markup=menu())


def create_dispatcher() -> Dispatcher:
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
