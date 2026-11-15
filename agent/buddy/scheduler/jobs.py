import asyncio
from contextlib import suppress
from functools import partial

from buddy.bot.rendering import keyboard, rich
from buddy.orchestration.service import AgentService


async def monitor_loop(service: AgentService, bot, seconds: int) -> None:
    """ИИ-крон imitation: scheduled access_events for users seen by this process."""
    while True:
        await asyncio.sleep(seconds)
        for chat, response in await service.monitor_all():
            if service.__dict__.get("channels") is not None:
                context = next((c for u, c in service.known.items() if service.chats.get(u) == chat), None)
                if context is not None:
                    with suppress(Exception):
                        await service.channels.background({"user_id": context.user_id}, response)
                continue
            with suppress(Exception):
                if bot is not None:
                    await rich(partial(bot.send_message, chat), response)


async def brief_loop(service: AgentService, bot, seconds: int = 60) -> None:
    """Scheduler of morning briefs (FR-H0-06): checks once a minute whose brief is due."""

    while True:
        await asyncio.sleep(seconds)
        for chat, response, context in await service.briefs_due():
            if service.__dict__.get("channels") is not None:
                ok = False
                try:
                    await service.channels.background({"user_id": context.user_id}, response)
                    ok = True
                except Exception:
                    ok = False
                finally:
                    with suppress(Exception):
                        await service.brief_delivered(response, context, ok)
                continue
            ok = True
            try:
                if bot is None:
                    raise RuntimeError("Telegram delivery unavailable")
                sent = await rich(
                    partial(bot.send_message, chat),
                    response,
                    reply_markup=keyboard(response.actions),
                    disable_web_page_preview=True,
                )
                if response.brief_id:
                    service.remember_brief(chat, sent.message_id, response.brief_id)
            except Exception:
                ok = False
            with suppress(Exception):
                await service.brief_delivered(response, context, ok)
