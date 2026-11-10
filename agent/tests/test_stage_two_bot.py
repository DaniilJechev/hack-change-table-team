from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from buddy.bot.handlers import create_dispatcher
from buddy.bot.identity import IdentityError, Profile
from buddy.llm.client import FakeLLM
from buddy.router.service import Router
from buddy.service import AgentService
from buddy.smoke import OfflineSession


class Harness:
    def __init__(self, *, demo=True, identity=None, llm=None):
        self.service = AgentService(Router(llm), demo_mode=demo)
        self.session = OfflineSession()
        self.bot = Bot(token="123456:OFFLINE_TEST_TOKEN", session=self.session)
        self.dispatcher = create_dispatcher(identity, self.service)
        self.index = 0

    async def send(self, text, *, user=42, group=False, missing=False):
        self.index += 1
        message = Message(
            message_id=self.index,
            date=datetime.now(UTC),
            chat=Chat(id=-99 if group else user, type="group" if group else "private"),
            from_user=None if missing else User(id=user, is_bot=False, first_name="Synthetic"),
            text=text,
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=self.index, message=message))
        return self.session.sent[-1]

    async def callback(self, data, *, user=42, missing_message=False):
        self.index += 1
        message = (
            None
            if missing_message
            else Message(
                message_id=self.index,
                date=datetime.now(UTC),
                chat=Chat(id=user, type="private"),
                from_user=User(id=123456, is_bot=True, first_name="Bot"),
            )
        )
        query = CallbackQuery(
            id=str(self.index),
            from_user=User(id=user, is_bot=False, first_name="Synthetic"),
            chat_instance="synthetic",
            message=message,
            data=data,
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=self.index, callback_query=query))


async def test_st2_demo_1_rt_07_telegram_flow_and_eight_buttons():
    app = Harness()
    try:
        assert "/demo" in (await app.send("/start")).text
        assert "H2 отвечает" in (await app.send("/help")).text
        assert "Нужна привязка" in (await app.send("Что нового?")).text
        ask = await app.send("/ask")
        assert len(ask.reply_markup.inline_keyboard) == 8
        assert "синтетические" in (await app.send("/demo")).text
        assert "Сбор" in (await app.send("Что нового?")).text
        changed = await app.send("/demo next")
        assert "Шаг 2/2" in app.session.sent[-2].text
        assert "Miro" in changed.text and "появился" in changed.text  # FR-H1-07 access_events
        assert "последний" in (await app.send("/demo next")).text
        assert "Шаг 1/2" in (await app.send("/demo reset")).text
        assert "не менялась" in (await app.send("/demo off")).text
        assert not app.service.demo.active(42)
    finally:
        await app.bot.session.close()


async def test_rt_04_callbacks_skip_router_and_rt_05_role_is_not_spoofable():
    app = Harness(llm=FakeLLM('{"case":"team_q1"}'))
    try:
        await app.send("/demo")
        app.service.router.route = AsyncMock(side_effect=AssertionError("Router forbidden"))
        await app.callback("case:team_q1")
        assert "Кто за что отвечает" in app.session.sent[-1].text
        await app.callback("case:interns_status")
        assert "только наставнику" in app.session.sent[-1].text
        await app.callback("case:write_code")
        assert "наставник" in app.session.sent[-1].text
        await app.callback("case:team_q1", missing_message=True)
        app.service.router.route.assert_not_called()
    finally:
        await app.bot.session.close()


@pytest.mark.parametrize("text", ["/demo", "/ask", "Кто мой наставник?"])
@pytest.mark.parametrize("kwargs", [{"group": True}, {"missing": True}])
async def test_st2_demo_1_group_and_missing_user_do_not_query_identity(text, kwargs):
    identity = AsyncMock()
    app = Harness(identity=identity)
    try:
        assert "личный чат" in (await app.send(text, **kwargs)).text
        identity.profile.assert_not_called()
        assert not app.service.demo.sessions
    finally:
        await app.bot.session.close()


async def test_st2_exec_1_real_profile_context_and_model_path():
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="mentor", display_name="Synthetic", role="mentor", team_id="payments"
    )
    app = Harness(demo=False, identity=identity, llm=FakeLLM('{"case":"interns_status"}'))
    try:
        assert "отключено" in (await app.send("/demo")).text
        assert "Запрос о доступах" in (await app.send("Сводка стажёров")).text
        identity.profile.assert_awaited_once_with(42)
        assert len(app.service.router.llm.calls) == 1
        identity.profile.side_effect = IdentityError()
        assert "Нужна привязка" in (await app.send("Кто мой наставник?")).text
    finally:
        await app.bot.session.close()


async def test_rt_03_clarification_and_mentor_buttons_are_explicit():
    app = Harness()
    try:
        await app.send("/demo")
        result = await app.send("Нужен доступ к Олегу")
        assert len(result.reply_markup.inline_keyboard) == 2
        result = await app.send("Напиши код сортировки")
        assert result.reply_markup.inline_keyboard[1][0].text == "Спросить наставника"
        await app.callback("mentor:contact")
        assert "Спросить наставника" in app.session.sent[-1].text
    finally:
        await app.bot.session.close()
