from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from buddy.bot.handlers import WELCOME, create_dispatcher
from buddy.config import Settings
from buddy.llm.client import create_llm


class OfflineSession(BaseSession):
    """Exercise the real aiogram dispatcher without contacting Telegram."""

    def __init__(self):
        super().__init__()
        self.sent: list[SendMessage] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method, timeout: int | None = None):
        if not isinstance(method, SendMessage):
            raise AssertionError("Unexpected network operation in offline smoke")
        self.sent.append(method)
        return Message(
            message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text=method.text
        )

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise AssertionError("Downloads are forbidden in offline smoke")
        yield b""  # pragma: no cover


async def smoke() -> None:
    session = OfflineSession()
    bot = Bot(token="123456:OFFLINE_TEST_TOKEN", session=session)
    try:
        update = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="Демо"),
                text="/start",
            ),
        )
        await create_dispatcher().feed_update(bot, update)
        assert len(session.sent) == 1 and session.sent[0].text == WELCOME
        llm = create_llm(Settings(_env_file=None, demo_mode=True))
        assert await llm.complete("demo", fallback="Шаблон") == "Шаблон"
        print("Offline /start + fake LLM: OK")
    finally:
        await bot.session.close()
