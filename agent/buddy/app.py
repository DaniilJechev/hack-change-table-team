import asyncio
from contextlib import suppress

from aiogram import Bot
from aiohttp import web

from buddy.bot.handlers import create_dispatcher
from buddy.bot.identity import IdentityClient
from buddy.config import Settings
from buddy.llm.client import create_llm
from buddy.router.service import Router
from buddy.service import AgentService


def health_app(mode: str, polling: asyncio.Task | None = None) -> web.Application:
    async def health(request: web.Request) -> web.Response:
        ok = polling is None or not polling.done()
        return web.json_response(
            {"status": "ok" if ok else "error", "service": "agent", "stage": "0", "mode": mode},
            status=200 if ok else 503,
        )

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    identity = (
        IdentityClient(settings.backend_url, settings.buddy_service_key.get_secret_value())
        if settings.backend_url and settings.buddy_service_key.get_secret_value()
        else None
    )
    service = AgentService(
        Router(None if settings.demo_mode else create_llm(settings)), demo_mode=settings.demo_mode
    )
    bot = None
    polling = None
    token = settings.telegram_bot_token.get_secret_value()
    if token:
        bot = Bot(token=token)
        # Poll only registered command updates; do not delete webhooks or pending updates.
        polling = asyncio.create_task(
            create_dispatcher(identity, service).start_polling(bot, handle_signals=False)
        )
    runner = web.AppRunner(health_app("telegram" if bot else "offline", polling))
    try:
        await runner.setup()
        await web.TCPSite(runner, settings.health_host, settings.health_port).start()
        waiter = asyncio.create_task((stop or asyncio.Event()).wait())
        try:
            watched = {waiter} if polling is None else {waiter, polling}
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if polling is not None and polling.done():
                await polling
        finally:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter
    finally:
        if polling is not None:
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
        if bot is not None:
            await bot.session.close()
        await runner.cleanup()
        if identity is not None:
            await identity.close()
