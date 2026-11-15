"""Best-effort Telegram activity while a handler awaits slow work."""

import asyncio
from contextlib import asynccontextmanager, suppress

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message


@asynccontextmanager
async def typing(message: Message, *, interval: float = 4.0):
    async def refresh():
        while True:
            try:
                async with asyncio.timeout(3):
                    await message.bot.send_chat_action(message.chat.id, "typing")
            except (TelegramAPIError, OSError, TimeoutError):
                pass  # Activity is supplementary; it must not discard the actual answer.
            await asyncio.sleep(interval)

    task = asyncio.create_task(refresh())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
