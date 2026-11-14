"""DESKTOP-02..05 shared channel acceptance, no Telegram polling or real LLM."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from buddy.channels.http import ClientAPI
from buddy.channels.repository import ChannelError, MemoryRepository
from buddy.channels.service import ChatService, owner
from buddy.config import Settings
from buddy.router.service import Router
from buddy.service import AgentService


async def setup():
    repo = MemoryRepository()
    agent = AgentService(Router(), demo_mode=True, settings=Settings())
    chat = ChatService(agent, repo)
    api = ClientAPI(chat, demo=True)
    app = web.Application(client_max_size=11 << 20)
    api.mount(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    result = await client.post("/client/v1/auth/demo", json={})
    token = (await result.json())["token"]
    client.session.headers["Authorization"] = "Bearer " + token
    profile = await repo.call("session", secret=token)
    return client, chat, profile


async def finish(chat):
    await asyncio.gather(*list(chat.tasks))


async def test_desktop_02_pair_expiry_revocation_and_one_time():
    clock = [1000]
    repo = MemoryRepository(clock=lambda: clock[0])
    d = await repo.call("device")
    assert (await repo.call("token", secret=d["device_secret"]))["pending"]
    await repo.call("approve", user_id="intern", code=d["user_code"])
    session = await repo.call("token", secret=d["device_secret"])
    assert (await repo.call("session", secret=session["token"]))["user_id"] == "intern"
    with pytest.raises(ChannelError):
        await repo.call("token", secret=d["device_secret"])
    await repo.call("logout", secret=session["token"])
    with pytest.raises(ChannelError):
        await repo.call("session", secret=session["token"])
    d = await repo.call("device")
    clock[0] += 301
    with pytest.raises(ChannelError):
        await repo.call("approve", user_id="other", code=d["user_code"])


async def test_desktop_03_shared_history_idempotency_and_errors():
    client, chat, p = await setup()
    try:
        # Represents a turn from the Telegram adapter; desktop sees the same history.
        await chat.submit(p, "telegram-1", {"text": "Какой у команды стек?"}, "telegram")
        await finish(chat)
        result = await client.get("/client/v1/chat/messages")
        rows = (await result.json())["items"]
        assert len(rows) == 2 and rows[0]["payload"]["channel"] == "telegram"
        for _ in range(2):
            result = await client.post("/client/v1/chat/requests", json={"id": "one", "text": "/access"})
            assert result.status == 202
        await finish(chat)
        result = await client.post("/client/v1/chat/requests", json={"id": "one", "text": "different"})
        assert result.status == 409
        result = await client.post(
            "/client/v1/chat/requests", json={"id": "bad", "text": "x", "user_id": "other"}
        )
        assert result.status == 400
        result = await client.get("/client/v1/chat/requests")
        assert len((await result.json())["items"]) == 2
        other = await client.post("/client/v1/auth/demo", json={})
        token2 = (await other.json())["token"]
        result = await client.get("/client/v1/chat/messages", headers={"Authorization": "Bearer " + token2})
        assert not (await result.json())["items"]
        chat.agent.handle = AsyncMock(side_effect=RuntimeError("secret must not leak"))
        await chat.submit(p, "failed", {"text": "question"})
        await finish(chat)
        failed = await chat.repository.call("get", user_id=owner(p), kind="request", id="failed")
        assert failed["status"] == "failed" and "secret" not in failed["error"]
    finally:
        await client.close()


async def test_desktop_03_h0_h1_h2_h3_h4_and_ownership():
    client, chat, p = await setup()
    try:
        for index, text in enumerate(
            [
                "/brief",
                "/access",
                "/ask",
                "Какой стек?",
                "Как проходит code review?",
                "/stats",
                "/settings",
                "/reset",
            ]
        ):
            await chat.submit(p, "case-" + str(index), {"text": text})
            await finish(chat)
            record = await chat.repository.call(
                "get", user_id=owner(p), kind="request", id="case-" + str(index)
            )
            assert record["status"] == "completed", record
        rows = (await chat.repository.call("list", user_id=owner(p), kind="message"))["items"]
        access = next(
            r
            for r in rows
            if any(a.get("data") == "request:demo-orders-miro" for a in r["payload"]["response"]["actions"])
        )
        await chat.submit(
            p,
            "request-miro",
            {"type": "action", "message_id": access["id"], "data": "request:demo-orders-miro"},
        )
        await finish(chat)
        assert (await chat.repository.call("get", user_id=owner(p), kind="request", id="request-miro"))[
            "status"
        ] == "completed"
        await chat.submit(
            p, "bad-action", {"type": "action", "message_id": access["id"], "data": "request:forbidden"}
        )
        await finish(chat)
        assert (await chat.repository.call("get", user_id=owner(p), kind="request", id="bad-action"))[
            "status"
        ] == "failed"
        form = FormData()
        form.add_field("file", Path("fixtures/equator/good.pdf").read_bytes(), filename="good.pdf")
        result = await client.post("/client/v1/chat/files", data=form, headers={"Idempotency-Key": "deck"})
        assert result.status == 202, await result.text()
        await finish(chat)
        record = await chat.repository.call("get", user_id=owner(p), kind="request", id="deck")
        assert record["status"] == "completed", record
        messages = (await chat.repository.call("list", user_id=owner(p), kind="message"))["items"]
        answer = next(m for m in messages if m["id"] == "deck:answer")
        assert "слайд" in answer["payload"]["response"]["result"]
        files = (await chat.repository.call("list", user_id=owner(p), kind="file"))["items"]
        result = await client.get("/client/v1/files/" + files[0]["id"])
        assert result.status == 200
        token = (await (await client.post("/client/v1/auth/demo", json={})).json())["token"]
        result = await client.get(
            "/client/v1/files/" + files[0]["id"], headers={"Authorization": "Bearer " + token}
        )
        assert result.status == 404
    finally:
        await client.close()


async def test_desktop_04_event_resume_read_and_session():
    client, chat, p = await setup()
    try:
        await chat.submit(p, "msg", {"text": "/brief"})
        await finish(chat)
        async with client.get("/client/v1/events") as result:
            assert result.status == 200
            data = await result.content.readuntil(b"\n\n")
            assert b"event: update" in data
        result = await client.put("/client/v1/chat/read", json={"message_id": "msg:answer"})
        assert result.status == 200
        result = await client.put("/client/v1/chat/read", json={"message_id": "other"})
        assert result.status == 404
        events = await chat.repository.call("events", user_id=owner(p), after=0)
        cursor = events["items"][-1]["seq"]
        assert not (await chat.repository.call("events", user_id=owner(p), after=cursor))["items"]
        result = await client.get("/client/v1/me")
        assert result.status == 200
        result = await client.delete("/client/v1/session")
        assert result.status == 200
        result = await client.get("/client/v1/chat/messages")
        assert result.status == 401
    finally:
        await client.close()


async def test_desktop_02_rate_and_malformed():
    client, chat, p = await setup()
    chat.api.demo = False
    try:
        for _ in range(10):
            assert (await client.post("/client/v1/auth/device", json={})).status == 200
        assert (await client.post("/client/v1/auth/device", json={})).status == 429
        assert (await client.post("/client/v1/auth/token", json={"device_secret": "bad"})).status == 400
        assert (await client.post("/client/v1/chat/requests", json={"id": "x", "text": ""})).status == 400
        assert (await client.post("/client/v1/chat/actions", json={"id": "x"})).status == 400
        assert (await client.get("/client/v1/info")).status == 200
    finally:
        await client.close()


async def test_desktop_03_h4_delivery_is_persistent_and_idempotent():
    client, chat, p = await setup()
    try:
        attachment = await chat.save_file(p, "good.pdf", Path("fixtures/equator/good.pdf").read_bytes())
        await chat.submit(p, "h4", {"attachment": attachment})
        await finish(chat)
        message = await chat.repository.call("get", user_id=owner(p), kind="message", id="h4:answer")
        action = next(a for a in message["response"]["actions"] if a["data"].startswith("equator:"))
        response = await chat.action(p, action["data"])
        assert response.feedback_saved
        rows = (await chat.repository.call("list", user_id=owner(p), kind="message"))["items"]
        count = len(rows)
        await chat.action(p, action["data"])
        assert len((await chat.repository.call("list", user_id=owner(p), kind="message"))["items"]) == count
        assert any(r["payload"]["attachments"] and r["payload"]["channel"] == "background" for r in rows)
        await chat.text(p, "/demo mentor")
        response = await chat.text(p, "/teamcard")
        assert "JSON" in response.result
        await chat.text(p, "/reset")
    finally:
        await client.close()


async def test_desktop_03_telegram_adapter_uses_shared_history():
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, Update, User
    from tests.test_stage_two_bot import Harness

    from buddy.bot.handlers import create_dispatcher

    harness = Harness()
    repo = MemoryRepository()
    chat = ChatService(harness.service, repo, bot=harness.bot)
    harness.service.channels = chat
    ClientAPI(chat, demo=True)
    harness.dispatcher = create_dispatcher(None, harness.service)
    try:
        await harness.send("/demo")
        await harness.send("/brief")
        await finish(chat)
        rows = (await repo.call("list", user_id="demo:42", kind="message"))["items"]
        assert len(rows) == 2 and rows[0]["payload"]["channel"] == "telegram"
        assert "Главное сегодня" in harness.session.sent[-1].text
        message_id = len(harness.session.sent)
        action = next(
            a
            for a in rows[0]["payload"]["response"]["actions"]
            if a.get("data", "").startswith("brief:positive:")
        )
        query = CallbackQuery(
            id="rating",
            from_user=User(id=42, is_bot=False, first_name="Synthetic"),
            chat_instance="synthetic",
            message=Message(message_id=message_id, date=datetime.now(UTC), chat=Chat(id=42, type="private")),
            data=action["data"],
        )
        await harness.dispatcher.feed_update(harness.bot, Update(update_id=100, callback_query=query))
        await finish(chat)
        assert harness.session.edits
        old = await repo.call("get", user_id="demo:42", kind="message", id=rows[0]["id"])
        assert len(old["response"]["actions"]) == 1
        assert old["response"]["actions"][0]["data"].startswith("brief:more:")
        assert (
            harness.session.edits[-1].reply_markup.inline_keyboard[0][0].callback_data
            == old["response"]["actions"][0]["data"]
        )
        # Group messages do not reach shared storage or agent tools.
        before = len(repo.records)
        await harness.send("question", group=True)
        assert len(repo.records) == before
    finally:
        await chat.close()
        await harness.bot.session.close()


async def test_desktop_02_backend_transport_is_private_and_errors_are_safe():
    import httpx

    from buddy.channels.repository import BackendRepository

    seen = []

    def transport(request):
        seen.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if request.url.path.endswith("/device"):
            return httpx.Response(200, json={"user_code": "SYNTHETIC"})
        return httpx.Response(503, json={})

    repository = BackendRepository("http://backend:8080", "synthetic-service-key")
    await repository.close()
    repository.client = httpx.AsyncClient(
        base_url="http://backend:8080",
        headers={"X-Buddy-Key": "synthetic-service-key"},
        transport=httpx.MockTransport(transport),
    )
    try:
        assert (await repository.call("device"))["user_code"] == "SYNTHETIC"
        with pytest.raises(ChannelError, match="not_found"):
            await repository.call("session", secret="synthetic-token")
        with pytest.raises(ChannelError) as error:
            await repository.call("unavailable")
        assert "synthetic-service-key" not in str(error.value)
        assert seen[0].url.path == "/internal/client/device"
        assert seen[0].headers["X-Buddy-Key"] == "synthetic-service-key"
    finally:
        await repository.close()


async def test_desktop_04_restart_emits_interruption_and_never_replays():
    repo = MemoryRepository()
    await repo.call("create", user_id="one", kind="request", id="r1", payload={"status": "running"})
    cursor = (await repo.call("events", user_id="one"))["items"][-1]["seq"]
    await repo.call("interrupt")
    events = (await repo.call("events", user_id="one", after=cursor))["items"]
    assert events[0]["payload"]["record"]["status"] == "interrupted"
    assert not (await repo.call("events", user_id="other"))["items"]


async def test_desktop_02_telegram_confirmation_requires_bound_private_identity():
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, Update, User
    from tests.test_stage_two_bot import Harness

    from buddy.bot.handlers import create_dispatcher
    from buddy.bot.identity import Profile

    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="intern", display_name="Synthetic", role="intern", team_id="demo-orders"
    )
    harness = Harness(demo=False, identity=identity)
    repo = MemoryRepository()
    harness.service.channels = ChatService(harness.service, repo, bot=harness.bot, identity=identity)
    ClientAPI(harness.service.channels, demo=False)
    harness.dispatcher = create_dispatcher(identity, harness.service)
    try:
        device = await repo.call("device")
        reply = await harness.send("/desktop " + device["user_code"])
        assert reply.reply_markup.inline_keyboard[0][0].callback_data == "desktop:" + device["user_code"]
        q = CallbackQuery(
            id="approve",
            from_user=User(id=42, is_bot=False, first_name="Synthetic"),
            chat_instance="synthetic",
            message=Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=42, type="private")),
            data="desktop:" + device["user_code"],
        )
        await harness.dispatcher.feed_update(harness.bot, Update(update_id=100, callback_query=q))
        result = await repo.call("token", secret=device["device_secret"])
        assert (await repo.call("session", secret=result["token"]))["user_id"] == "intern"
        assert harness.session.edits
        count = identity.profile.await_count
        await harness.send("/desktop " + device["user_code"], group=True)
        assert identity.profile.await_count == count
    finally:
        await harness.bot.session.close()


async def test_desktop_05_redirect_uses_only_backend_stored_target():
    import httpx

    from buddy.channels.repository import BackendRepository

    repository = BackendRepository("http://backend:8080", "synthetic")
    await repository.close()
    repository.client = httpx.AsyncClient(
        base_url="http://backend:8080",
        follow_redirects=False,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"Location": "https://github.com/example/repo/pull/1"}
            )
        ),
    )
    try:
        assert await repository.redirect("a" * 32) == "https://github.com/example/repo/pull/1"
    finally:
        await repository.close()
    client, chat, p = await setup()
    try:
        assert (await client.get("/r/" + "a" * 32, allow_redirects=False)).status == 404
        chat.api.demo = False

        async def resolve(token, *, head=False):
            assert token == "a" * 32
            return "https://github.com/example/repo/pull/1"

        chat.repository.redirect = resolve
        result = await client.get("/r/" + "a" * 32, allow_redirects=False)
        assert result.status == 302 and result.headers["Location"] == "https://github.com/example/repo/pull/1"
        result = await client.head("/r/" + "a" * 32, allow_redirects=False)
        assert result.status == 302
    finally:
        await client.close()


async def test_desktop_04_scheduled_brief_recovers_from_storage_failure():
    from buddy.app import brief_loop
    from buddy.schemas import ExecutorResponse

    service = AsyncMock()
    service.channels = AsyncMock()
    service.channels.background.side_effect = ChannelError("unavailable")
    context = object()
    response = ExecutorResponse(result="synthetic", brief_id="b1")
    service.briefs_due.side_effect = [[(42, response, context)], asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await brief_loop(service, None, seconds=0)
    service.brief_delivered.assert_awaited_once_with(response, context, False)


@pytest.mark.parametrize(
    "text,callback",
    [("Напиши код сортировки", "mentor:contact"), ("Нужен доступ к Олегу", "case:access_status")],
)
async def test_desktop_03_preserves_telegram_clarification_actions(text, callback):
    client, chat, p = await setup()
    try:
        response = await chat.text(p, text)
        assert callback in [action.data for action in response.actions]
    finally:
        await client.close()
