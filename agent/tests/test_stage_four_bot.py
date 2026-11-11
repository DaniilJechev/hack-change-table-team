"""Stage 4 end-to-end in Telegram (offline session): H0 brief and H3 playbook, demo and live wiring."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import Chat, Message, Update, User
from tests.test_stage_two_bot import Harness

from buddy.app import brief_loop
from buddy.bot.identity import Profile
from buddy.data.backend import BackendData
from buddy.data.base import DataError
from buddy.data.seed import SeedData
from buddy.demo.controller import DemoController
from buddy.runtime import Runtime


def buttons(message) -> list:
    markup = message.reply_markup
    return [button for row in markup.inline_keyboard for button in row] if markup else []


def data_of(message, prefix: str) -> str:
    return next(b.callback_data for b in buttons(message) if (b.callback_data or "").startswith(prefix))


async def reply(app: Harness, text: str, to: int, user: int = 42):
    app.index += 1
    chat = Chat(id=user, type="private")
    original = Message(message_id=to, date=datetime.now(UTC), chat=chat, text="бриф")
    message = Message(
        message_id=1000 + app.index,
        date=datetime.now(UTC),
        chat=chat,
        from_user=User(id=user, is_bot=False, first_name="Synthetic"),
        text=text,
        reply_to_message=original,
    )
    await app.dispatcher.feed_update(app.bot, Update(update_id=app.index, message=message))
    return app.session.sent[-1]


async def test_st4_demo_1_brief_and_playbook_full_flow_in_telegram():
    app = Harness()
    try:
        await app.send("/demo")
        brief = await app.send("/brief")
        assert brief.text.startswith("Демо: синтетический сценарий.\nБриф на 11.11:")
        assert "▶ Главное сегодня: исправить замечания в PR #45" in brief.text
        assert brief.text.count("https://example.invalid/") == 5 and "Источники:" in brief.text
        labels = [button.text for button in buttons(brief)]
        assert labels == ["Ещё 3", "✅ Прочитал", "👍", "👎"]
        brief_id = data_of(brief, "brief:read:").rsplit(":", 1)[1]
        assert app.service.brief_messages == {(42, len(app.session.sent)): brief_id}
        assert "отправлено 1, прочитано 0%" in (await app.send("/stats")).text  # no signal yet
        await app.callback(data_of(brief, "brief:more:"))
        assert "Остальные изменения:\n6." in app.session.sent[-1].text
        assert "прочитано 100%" in (await app.send("/stats")).text  # opening the rest is a read signal
        await app.callback(data_of(brief, "brief:positive:"))
        assert "Спасибо за оценку брифа" in app.session.sent[-1].text
        assert "Изменений нет." in (await app.send("Что нового?")).text
        assert "08:30" in (await app.send("/settings brief 08:30")).text
        assert "Тихий режим: включён" in (await app.send("/settings quiet on")).text

        await app.send("/demo next")
        await app.send("/demo next")  # the next morning of the synthetic scenario
        morning = await app.send("/brief")
        assert (
            "Бриф на 12.11" in morning.text and "срок сегодня" in morning.text and "одобрен" in morning.text
        )
        await reply(app, "Что такое Zero Bug Policy?", to=len(app.session.sent))
        answer = app.session.sent[-1]
        assert "Zero Bug Policy" in answer.text and "может быть устаревшим" in answer.text
        assert "github.com/avito-tech/playbook/blob/" in answer.text
        assert "development-principles.md#мы-работаем-по-zero-bug-policy" in answer.text
        record = app.service.demo.sessions[42].data._briefs()["briefs"]
        newest = max(record.values(), key=lambda item: item["created_at"] + item["id"])
        assert (
            any(event["kind"] == "reply" for item in record.values() for event in item["events"]) and newest
        )
        await app.callback(data_of(answer, "rate:bot:"))
        assert "Спасибо" in app.session.sent[-1].text
        assert "только наставнику" not in (await app.send("/stats")).text
        await app.send("/demo mentor")
        assert "Статистика брифов" in (await app.send("/stats")).text
    finally:
        await app.bot.session.close()


async def test_st4_demo_1_brief_delivery_failure_keeps_changes_for_the_next_brief():
    app = Harness()
    try:
        await app.send("/demo")
        original = app.session.make_request
        failed = {"count": 0}

        async def flaky(bot, method, timeout=None):
            if "Главное сегодня" in getattr(method, "text", "") and not failed["count"]:
                failed["count"] += 1
                raise RuntimeError("Telegram is down")
            return await original(bot, method, timeout)

        app.session.make_request = flaky
        with pytest.raises(RuntimeError):
            await app.send("/brief")
        state = app.service.demo.sessions[42].data._briefs()["briefs"]
        assert [item["status"] for item in state.values()] == ["failed"]
        again = await app.send("/brief")
        assert "запрошены изменения" in again.text  # collected again since the last *sent* brief
        assert sorted(item["status"] for item in state.values()) == ["failed", "sent"]
    finally:
        await app.bot.session.close()


async def test_st4_live_1_scheduled_brief_loop_and_flows_without_runtime():
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="demo-daniil", display_name="Даниил", role="intern", team_id="demo-orders"
    )
    app = Harness(demo=False, identity=identity)
    try:
        for text, expected in [("/stats", "недоступна"), ("/settings", "недоступны")]:
            assert expected in (await app.send(text)).text
        await app.callback("brief:read:br-x")
        assert "Брифы сейчас недоступны" in app.session.sent[-1].text
        assert "пока не подключены" in (await app.send("/brief")).text  # no runtime: safe template
        assert await app.service.briefs_due() == []

        controller = DemoController(True)
        controller.command(7)
        fixtures = controller.runtime(7)
        fixtures.data = SeedData()
        fixtures.brief_now = fixtures.brief_now.replace(hour=10, minute=5)  # after standup − 30 min
        app.service.live = fixtures
        await app.send("/access")  # the user becomes known to this process
        task = asyncio.create_task(brief_loop(app.service, app.bot, seconds=0))
        for _ in range(200):
            await asyncio.sleep(0)
            if any("Утренний бриф" in message.text for message in app.session.sent):
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        morning = next(message for message in app.session.sent if "Утренний бриф" in message.text)
        assert morning.chat_id == 42 and [b.text for b in buttons(morning)][1] == "✅ Прочитал"
        assert (await fixtures.data.last_brief_sent("demo-daniil")) is not None
        assert await app.service.briefs_due() == []  # once per day
        await reply(app, "Спасибо!", to=list(app.service.brief_messages)[0][1])
        assert fixtures.data.brief_counts("demo-daniil")["read"] == 1
    finally:
        await app.bot.session.close()


async def test_st4_live_1_brief_loop_marks_failed_delivery():
    service = AsyncMock()
    context = object()
    response = type("R", (), {"brief_id": "br-1", "actions": [], "result": "бриф", "links": []})()
    service.briefs_due.side_effect = [[(42, response, context)], asyncio.CancelledError()]
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("blocked by user")
    with pytest.raises(asyncio.CancelledError):
        await brief_loop(service, bot, seconds=0)
    service.brief_delivered.assert_awaited_once_with(response, context, False)
    service.remember_brief.assert_not_called()


async def test_st4_data_1_backend_brief_rest_and_local_state():
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.headers["X-Buddy-User"], body))
        if request.url.path == "/api/v1/briefs":
            return httpx.Response(200, json={"id": body["id"], "created_at": "2026-11-11T06:00:00Z"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"id": body["id"], "created_at": "2026-11-11T06:05:00Z"})
        return httpx.Response(
            200, json={"id": "br 1", "status": body["status"], "sent_at": "2026-11-11T06:01:00Z"}
        )

    async def mcp(user_id, tool, arguments):
        if tool == "interns_list":
            return {"items": [{"user_id": "u1", "display_name": "Стажёр"}]}
        return {"user_id": user_id, "display_name": "Наставник", "role": "mentor", "team_id": "t"}

    data = BackendData("http://backend:8080", "service-key", transport=httpx.MockTransport(handler), mcp=mcp)
    items = [{"id": "i-1", "source": "jira"}]
    await data.save_brief("u1", "br 1", items)
    await data.brief_status("u1", "br 1", "sent", {"events": {"e": {"start": "x"}}})
    await data.brief_event("u1", "br 1", "click", "i-1")
    await data.brief_event("u1", "br 1", "read")
    assert [entry[:2] for entry in seen] == [
        ("POST", "/api/v1/briefs"),
        ("PATCH", "/api/v1/briefs/br 1"),
        ("POST", "/api/v1/briefs/br 1/events"),
        ("POST", "/api/v1/briefs/br 1/events"),
    ]
    assert seen[0][3] == {"id": "br 1", "items": items} and seen[1][3] == {"status": "sent"}
    assert seen[2][3]["kind"] == "click" and seen[2][3]["item_id"] == "i-1" and "item_id" not in seen[3][3]
    assert await data.last_brief_sent("u1") == "2026-11-11T06:01:00Z"
    assert await data.brief_seen("u1") == {"i-1"} and await data.calendar_snapshot("u1")
    assert await data.brief_stats("mentor") == [
        {
            "user_id": "u1",
            "display_name": "Стажёр",
            "sent": 1,
            "read": 1,
            "clicked": 1,
            "positive": 0,
            "negative": 0,
        }
    ]
    assert (await data.set_brief_settings("u1", time="08:00", unknown=1)) == {"time": "08:00", "quiet": False}
    await data.close()


async def test_st4_data_1_brief_state_rules():
    data = SeedData()
    await data.save_brief("demo-daniil", "br-1", [{"id": "i-1"}])
    with pytest.raises(DataError) as conflict:
        await data.save_brief("demo-daniil", "br-1", [])
    assert conflict.value.kind == "conflict"
    with pytest.raises(DataError):
        await data.brief_event("demo-daniil", "br-1", "read")  # a draft cannot be read
    with pytest.raises(DataError):
        await data.brief_status("demo-daniil", "br-1", "draft")
    with pytest.raises(DataError):
        await data.brief("demo-anna", "br-1")  # another user's brief is not visible
    await data.brief_status("demo-daniil", "br-1", "sent", at="2026-11-11T09:00:00+03:00")
    await data.brief_status("demo-daniil", "br-1", "failed")  # sent is never downgraded
    assert (await data.brief("demo-daniil", "br-1"))["status"] == "sent"
    with pytest.raises(DataError):
        await data.brief_event("demo-daniil", "br-1", "shared")
    assert await data.brief_stats("demo-anna") == [
        {
            "user_id": "demo-daniil",
            "display_name": "Даниил",
            "sent": 1,
            "read": 0,
            "clicked": 0,
            "positive": 0,
            "negative": 0,
        }
    ]
    runtime = Runtime(data=data, gateway=None, telegram=None)
    assert runtime.brief_now is None
