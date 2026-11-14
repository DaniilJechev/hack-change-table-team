import asyncio
from contextlib import suppress
from functools import partial

from aiogram import Bot
from aiohttp import web

from buddy.access.checker import BotTelegram
from buddy.bot.handlers import create_dispatcher
from buddy.bot.identity import IdentityClient
from buddy.bot.stage_two import keyboard, rich
from buddy.config import Settings
from buddy.data.backend import BackendData
from buddy.data.traces import TraceSink
from buddy.llm.client import create_llm
from buddy.memory import Memory, RedisStore
from buddy.router.service import Router
from buddy.runtime import Runtime
from buddy.service import AgentService


def health_app(mode: str, polling: asyncio.Task | None = None) -> web.Application:
    async def health(request: web.Request) -> web.Response:
        ok = polling is None or not polling.done()
        return web.json_response(
            {"status": "ok" if ok else "error", "service": "agent", "stage": "0", "mode": mode},
            status=200 if ok else 503,
        )

    app = web.Application(client_max_size=11 << 20)
    app.router.add_get("/health", health)
    return app


def live_hub(audit_sink=None):  # pragma: no cover - live accounts only
    from pathlib import Path

    from buddy.demo.controller import DATA
    from buddy.mcp_hub.config import load_servers
    from buddy.mcp_hub.fixtures import FixtureStore
    from buddy.mcp_hub.hub import Hub

    return Hub(
        load_servers(Path(".env")), FixtureStore(DATA / "fixtures"), demo_mode=False, audit_sink=audit_sink
    )


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


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    identity = (
        IdentityClient(settings.backend_url, settings.buddy_service_key.get_secret_value())
        if settings.backend_url and settings.buddy_service_key.get_secret_value()
        else None
    )
    llm = None if settings.demo_mode and not settings.llm_in_demo else create_llm(settings)
    memory = None
    if settings.redis_url and not settings.demo_mode:  # pragma: no cover - needs a Redis server
        memory = Memory(
            RedisStore.from_url(settings.redis_url, settings.memory_max_turns, settings.memory_ttl_seconds),
            max_turns=settings.memory_max_turns,
            ttl_seconds=settings.memory_ttl_seconds,
            enabled=settings.memory_enabled,
        )
    key = settings.buddy_service_key.get_secret_value()
    # FR-TR-01: turns are written to the backend in demo mode too, whenever it is configured.
    traces = TraceSink(settings.backend_url, key) if settings.backend_url and key else None
    service = AgentService(
        Router(llm), demo_mode=settings.demo_mode, settings=settings, llm=llm, memory=memory, traces=traces
    )
    service.channels = None
    channel_repo = None
    if settings.client_api_enabled:
        from buddy.channels.repository import BackendRepository, MemoryRepository
        from buddy.channels.service import ChatService

        channel_repo = (
            MemoryRepository() if settings.demo_mode else BackendRepository(settings.backend_url, key)
        )
        service.channels = ChatService(service, channel_repo, identity=identity)
    bot = None
    polling = None
    monitor = None
    briefs = None
    data = None
    token = settings.telegram_bot_token.get_secret_value()
    if token:
        bot = Bot(token=token)
        if service.channels is not None:
            service.channels.bot = bot
    if not settings.demo_mode and identity is not None:  # pragma: no cover - live accounts only
        data = BackendData(settings.backend_url, settings.buddy_service_key.get_secret_value())
        service.live = Runtime(data=data, gateway=live_hub(data.record_mcp_call), telegram=BotTelegram(bot))
        monitor = asyncio.create_task(monitor_loop(service, bot, settings.access_monitor_seconds))
        briefs = asyncio.create_task(brief_loop(service, bot))
    if bot is not None:
        polling = asyncio.create_task(
            create_dispatcher(identity, service).start_polling(bot, handle_signals=False)
        )
    application = health_app("telegram" if bot else "offline", polling)
    if service.channels is not None:
        from buddy.channels.http import ClientAPI

        ClientAPI(service.channels, demo=settings.demo_mode).mount(application)
    runner = web.AppRunner(application)
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
        for task in (polling, monitor, briefs):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if service.channels is not None:
            await service.channels.close()
        if channel_repo is not None and hasattr(channel_repo, "close"):
            await channel_repo.close()
        if data is not None:  # pragma: no cover - live accounts only
            await data.close()
        if bot is not None:
            await bot.session.close()
        await runner.cleanup()
        if identity is not None:
            await identity.close()
        if traces is not None:
            await traces.close()
