import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from aiogram import Bot
from aiogram.types import Chat, Document, Message, Update, User
from test_stage_two_bot import Harness

from buddy.bot.identity import Profile
from buddy.bot.teamcard import LimitedUpload
from buddy.data import BackendData, DataError, SeedData
from buddy.demo.controller import DemoController
from buddy.router.service import Router
from buddy.service import AgentService

ROOT = Path(__file__).resolve().parents[2]


def runtime():
    demo = DemoController(True)
    demo.command(1)
    return demo.runtime(1)


async def test_fr_h2_01_mentor_uploads_versioned_yaml_and_sees_field_errors():
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="demo-anna", team_id="demo-orders", role="mentor", display_name="Анна"
    )
    app = Harness(demo=False, identity=identity)
    app.service.live = runtime()
    source = yaml.safe_load((ROOT / "seed/team_cards/demo-orders.yaml").read_text(encoding="utf-8"))
    source["name"] += " v2"
    text = yaml.safe_dump(source, allow_unicode=True, sort_keys=False)
    try:
        assert "YAML" in (await app.send("/teamcard")).text
        assert "Версия: 2" in (await app.send(text)).text
        assert "Версия: 2" in (await app.send("/teamcard\n" + text)).text
        del source["people"][2]["role"]
        result = await app.send("/teamcard\n" + yaml.safe_dump(source, allow_unicode=True))
        assert "people[2].role" in result.text and "обязательное" in result.text
        identity.profile.return_value = Profile(
            user_id="demo-daniil", team_id="demo-orders", role="intern", display_name="Даниил"
        )
        assert "только наставнику" in (await app.send("/teamcard")).text
    finally:
        await app.bot.session.close()


async def test_fr_h2_01_file_upload_bounds_and_profile_scope(monkeypatch):
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="demo-anna", team_id="demo-orders", role="mentor", display_name="Анна"
    )
    app = Harness(demo=False, identity=identity)
    app.service.live = runtime()
    source = (ROOT / "seed/team_cards/demo-orders.yaml").read_bytes()

    async def download(*args, **kwargs):
        kwargs["destination"].write(source)

    mocked = AsyncMock(side_effect=download)
    monkeypatch.setattr(Bot, "download", mocked)

    async def send_document(size):
        app.index += 1
        message = Message(
            message_id=app.index,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            from_user=User(id=42, is_bot=False, first_name="Synthetic"),
            document=Document(
                file_id="synthetic", file_unique_id="synthetic", file_name="card.yaml", file_size=size
            ),
        )
        await app.dispatcher.feed_update(app.bot, Update(update_id=app.index, message=message))
        return app.session.sent[-1].text

    try:
        assert "Сначала" in await send_document(len(source))
        await app.send("/teamcard")
        assert "загружена" in await send_document(len(source))
        await app.send("/teamcard")
        assert "больше 1 МиБ" in await send_document((1 << 20) + 1)
        assert mocked.await_count == 1
        await app.send("/teamcard")
        identity.profile.return_value = Profile(
            user_id="demo-oleg", team_id="demo-orders", role="teamlead", display_name="Олег"
        )
        assert "Сначала" in await send_document(len(source))
    finally:
        await app.bot.session.close()
    with LimitedUpload() as stream:
        with pytest.raises(ValueError):
            stream.write(b"x" * ((1 << 20) + 1))


async def test_fr_h2_08_bot_ratings_are_private_separate_and_not_faq_authorship():
    data = SeedData()
    await data.record_bot_answer(
        "demo-daniil",
        "b-1",
        "Кто наставник?",
        "Наставник — Анна",
        ["https://example.invalid/team/demo-orders/card"],
    )
    await data.rate_bot_answer("demo-daniil", "b-1", True)
    record = await data.question("demo-daniil", "b-1")
    assert record["bot_helpful"] is True and record["helpful"] is None and record["answer"] == ""
    with pytest.raises(DataError):
        await data.rate_bot_answer("demo-kirill", "b-1", True)
    with pytest.raises(DataError):
        await data.publish_faq("demo-daniil", "f-1", "b-1")
    with pytest.raises(DataError):
        await data.record_bot_answer(
            "demo-daniil",
            "b-1",
            "Кто наставник?",
            "Подмена",
            ["https://example.invalid/team/demo-orders/card"],
        )


async def test_st3_flags_disable_monitor_and_human_publication():
    service = AgentService(Router(), demo_mode=True)
    service.demo.command(1)
    state = service.demo.runtime(1).data
    state.team_flags["demo-orders"] = {"access_monitoring": False, "team_escalation": False}
    service.executors.access.monitor = AsyncMock()
    assert await service.monitor(service.demo.context(1)) is None
    service.executors.access.monitor.assert_not_called()
    with pytest.raises(DataError):
        await state.create_question("demo-daniil", "q-flags", "Почему?", "demo-petr")
    state.team_flags["demo-orders"] = {"faq_publication": False}
    await state.create_question("demo-daniil", "q-flags", "Почему?", "demo-petr")
    await state.answer_question("demo-petr", "q-flags", "Учебный ответ")
    with pytest.raises(DataError):
        await state.publish_faq("demo-petr", "f-flags", "q-flags")


async def test_backend_stage_three_upload_rating_and_faq_contracts():
    requests = []

    def handle(request):
        assert request.headers["X-Buddy-User"] == "demo-anna"
        requests.append(request)
        if request.url.path.endswith("team-card"):
            assert request.content == b"synthetic: true"
            return httpx.Response(200, json={"version": 2})
        return httpx.Response(200, json={"id": "b-1", "access_monitoring": False})

    data = BackendData("http://backend", "synthetic-key", transport=httpx.MockTransport(handle))
    try:
        assert (await data.upload_team_card("demo-anna", b"synthetic: true"))["version"] == 2
        assert (await data.flags("demo-anna"))["access_monitoring"] is False
        await data.record_bot_answer(
            "demo-anna", "b-1", "вопрос", "ответ", ["https://example.invalid/source/card"]
        )
        await data.rate_bot_answer("demo-anna", "b-1", True)
        assert requests[-1].method == "PATCH" and json.loads(requests[-1].content) == {"helpful": True}
    finally:
        await data.close()
    data = BackendData(
        "http://backend",
        "key",
        mcp=AsyncMock(return_value={"items": [{"id": "f-1", "source_path": "/api/v1/faq/f-1"}]}),
    )
    try:
        assert (await data.faq_search("demo-anna", "x"))[0]["source_path"] == "http://backend/api/v1/faq/f-1"
    finally:
        await data.close()


async def test_st3_demo_role_1_presenter_can_check_mentor_summary_and_upload():
    app = Harness()
    try:
        await app.send("/demo")
        await app.send("/access")
        assert "demo-anna" in (await app.send("/demo mentor")).text
        summary = await app.send("/interns")
        assert "Даниил — 4/5" in summary.text
        assert "YAML" in (await app.send("/teamcard")).text
        source = (ROOT / "seed/team_cards/demo-orders.yaml").read_text(encoding="utf-8")
        assert "загружена" in (await app.send(source)).text
        await app.send("/demo intern")
        assert "только наставнику" in (await app.send("/interns")).text
    finally:
        await app.bot.session.close()


async def test_fr_h1_12_restart_uses_persisted_snapshot():
    from buddy.access.service import AccessService

    demo = DemoController(True)
    demo.command(1)
    context = demo.context(1)
    assert await AccessService().monitor(context, demo.runtime(1)) is None
    demo.command(1, "next")
    restarted = AccessService()
    response = await restarted.monitor(context, demo.runtime(1))
    assert "Miro" in response.result and "появился" in response.result
    assert await restarted.monitor(context, demo.runtime(1)) is None
