import asyncio
import json
import socket
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot
from aiogram.methods import GetMe
from aiogram.types import Chat, Message, Update, User
from pydantic import ValidationError

from buddy import __main__
from buddy.app import health_app, run
from buddy.bot.handlers import HELP, WELCOME, create_dispatcher
from buddy.config import Settings
from buddy.llm.client import CompatibleLLM, FakeLLM, create_llm
from buddy.schemas import ExecutorResponse, RouteDecision, UserProfile
from buddy.smoke import OfflineSession, smoke


def config(**kwargs):
    return Settings(_env_file=None, **kwargs)


def live(**kwargs):
    return config(
        demo_mode=False,
        telegram_bot_token="123456:LOCAL_TEST_TOKEN",
        llm_provider="openrouter",
        llm_model="test/model",
        llm_api_key="test-secret",
        **kwargs,
    )


async def test_st0_config_1_demo_without_keys(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    settings = config()
    assert isinstance(create_llm(settings), FakeLLM)
    fake = FakeLLM(response="fake response")
    assert await fake.complete("prompt", fallback="fallback") == "fake response"
    assert fake.calls == ["prompt"]
    await smoke()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"demo_mode": False},
        {"demo_mode": False, "telegram_bot_token": "123456:TEST"},
        {
            "demo_mode": False,
            "telegram_bot_token": "123456:TEST",
            "llm_provider": "compatible",
            "llm_model": "model",
            "llm_api_key": "key",
        },
        {"llm_timeout_seconds": 21},
        {"llm_timeout_seconds": 0},
        {"llm_provider": "unknown"},
    ],
)
def test_st0_config_1_invalid_live_configuration(kwargs):
    with pytest.raises(ValidationError):
        config(**kwargs)


def test_st0_config_1_secrets_and_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "compatible")
    monkeypatch.setenv("LLM_MODEL", "local/model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    settings = config(llm_api_key="secret-not-in-repr", telegram_bot_token="another-secret")
    assert settings.llm_model == "local/model"
    assert settings.llm_provider == "compatible"
    assert "secret-not-in-repr" not in repr(settings)
    assert "another-secret" not in repr(settings)
    assert settings.embeddings_model


async def test_nfr_llm_1_st0_config_1_switch_provider():
    settings = live()
    assert isinstance(create_llm(settings), CompatibleLLM)
    settings.llm_provider = "compatible"
    settings.llm_base_url = "https://llm.example.test/v1/"

    def handle(request):
        assert str(request.url) == "https://llm.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(request.content)["model"] == "test/model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    client = CompatibleLLM(settings, httpx.MockTransport(handle))
    assert await client.complete("prompt", fallback="fallback") == "answer"


@pytest.mark.parametrize(
    "kind",
    [
        "server",
        "unauthorized",
        "invalid_json",
        "missing",
        "empty",
        "wrong_type",
        "timeout",
        "slow",
        "connection",
    ],
)
async def test_nfr_fb_1_st0_config_1_llm_failure(kind):
    settings = live()
    settings.llm_timeout_seconds = 0.01

    async def handle(request):
        assert str(request.url).startswith("https://openrouter.ai/api/v1/")
        if kind == "server":
            return httpx.Response(503)
        if kind == "unauthorized":
            return httpx.Response(401)
        if kind == "invalid_json":
            return httpx.Response(200, text="not json")
        if kind == "timeout":
            raise httpx.ReadTimeout("test")
        if kind == "connection":
            raise httpx.ConnectError("test")
        if kind == "slow":
            await asyncio.sleep(0.1)
        value = {
            "missing": [],
            "empty": [{"message": {"content": " "}}],
            "wrong_type": [{"message": {"content": []}}],
        }.get(kind, [])
        return httpx.Response(200, json={"choices": value})

    assert (
        await CompatibleLLM(settings, httpx.MockTransport(handle)).complete("x", fallback="template")
        == "template"
    )


@pytest.mark.parametrize(
    "text,expected", [("/start", WELCOME), ("/start invitation", WELCOME), ("/help", HELP)]
)
async def test_st0_bot_1_command(text, expected):
    session = OfflineSession()
    bot = Bot(token="123456:LOCAL_TEST_TOKEN", session=session)
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, first_name="Synthetic", is_bot=False),
            text=text,
        ),
    )
    try:
        await create_dispatcher().feed_update(bot, update)
        assert len(session.sent) == 1
        assert session.sent[0].text == expected
        assert session.sent[0].reply_markup.keyboard[0][0].text == "/help"
    finally:
        await bot.session.close()


async def test_st0_bot_1_offline_transport_rejects_network():
    session = OfflineSession()
    bot = Bot(token="123456:LOCAL_TEST_TOKEN", session=session)
    with pytest.raises(AssertionError):
        await session.make_request(bot, GetMe())
    with pytest.raises(AssertionError):
        await anext(session.stream_content())
    await session.close()


@pytest.mark.parametrize(
    "factory,data",
    [
        (UserProfile, {"user_id": "u", "team_id": "t", "display_name": "name", "role": "admin"}),
        (RouteDecision, {"case": "write_code"}),
        (ExecutorResponse, {"result": "ok", "extra": True}),
        (ExecutorResponse, {"result": "ok", "citations": [{"url": "https://invented.test"}]}),
    ],
)
def test_st0_schema_1_rejects_invalid_contracts(factory, data):
    with pytest.raises(ValidationError):
        factory(**data)


def test_st0_schema_1_valid_contracts():
    assert RouteDecision(case="help").params == {}
    assert (
        ExecutorResponse(result="ok", citations=[{"source_id": "source-1"}]).citations[0].source_id
        == "source-1"
    )
    assert UserProfile(user_id="u", team_id="t", display_name="name", role="intern").role == "intern"


async def test_st0_infra_1_agent_health_states():
    task = asyncio.create_task(asyncio.sleep(100))
    try:
        for polling, expected in [(None, 200), (task, 200)]:
            app = health_app("test", polling)
            handler = next(iter(app.router.routes())).handler
            assert (await handler(None)).status == expected
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        app = health_app("telegram", task)
        assert (await next(iter(app.router.routes())).handler(None)).status == 503
    finally:
        task.cancel()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("telegram", [False, True])
async def test_st0_infra_1_agent_start_and_shutdown(monkeypatch, telegram):
    port = free_port()
    settings = config(
        health_host="127.0.0.1",
        health_port=port,
        backend_url="http://backend",
        buddy_service_key="synthetic-service-key",
        telegram_bot_token="123456:LOCAL_TEST_TOKEN" if telegram else "",
    )
    polling = AsyncMock(side_effect=lambda *args, **kwargs: None)
    if telegram:

        async def wait_forever(*args, **kwargs):
            await asyncio.Event().wait()

        polling.side_effect = wait_forever
        monkeypatch.setattr("aiogram.Dispatcher.start_polling", polling)
    stop = asyncio.Event()
    task = asyncio.create_task(run(settings, stop))
    try:
        async with httpx.AsyncClient() as client:
            for attempt in range(100):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/health")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.01)
            else:
                pytest.fail("agent health server did not start")
            assert response.status_code == 200
            assert (await client.get(f"http://127.0.0.1:{port}/missing")).status_code == 404
            assert response.json()["mode"] == ("telegram" if telegram else "offline")
    finally:
        stop.set()
        await asyncio.wait_for(task, 3)
    if telegram:
        polling.assert_awaited_once()


async def test_st0_infra_1_agent_polling_failure(monkeypatch):
    monkeypatch.setattr(
        "aiogram.Dispatcher.start_polling", AsyncMock(side_effect=RuntimeError("test failure"))
    )
    with pytest.raises(RuntimeError, match="test failure"):
        await run(config(telegram_bot_token="123456:LOCAL_TEST_TOKEN", health_port=free_port()))


def test_st0_qa_1_cli_smoke(monkeypatch):
    monkeypatch.setattr("sys.argv", ["buddy", "--smoke"])
    __main__.main()


def test_st0_qa_1_cli_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["buddy"])
    fake_run = AsyncMock()
    monkeypatch.setattr(__main__, "run", fake_run)
    __main__.main()
    fake_run.assert_awaited_once()
