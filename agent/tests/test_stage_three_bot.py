"""Stage 3 end-to-end in Telegram (offline session): H1 and H2 flows in demo and live modes."""

from unittest.mock import AsyncMock

from tests.test_stage_two_bot import Harness

from buddy.bot.identity import Profile
from buddy.data.seed import SeedData
from buddy.demo.controller import DemoController
from buddy.runtime import Runtime


def buttons(message) -> list:
    markup = message.reply_markup
    return [button for row in markup.inline_keyboard for button in row] if markup else []


def callback_of(messages, prefix: str) -> str:
    for message in reversed(messages):
        for button in buttons(message):
            if button.callback_data and button.callback_data.startswith(prefix):
                return button.callback_data
    raise AssertionError("no button " + prefix)


async def test_st3_demo_1_h1_and_h2_full_flow_in_telegram():
    app = Harness()
    try:
        await app.send("/demo")
        status = await app.send("Что с моими доступами?")
        assert "Ваши доступы" in status.text and "Источники:" in status.text
        assert any(button.url for button in buttons(status))
        await app.callback(callback_of([status], "mark:demo-orders-miro"))
        assert "Отметил" in app.session.sent[-1].text
        await app.callback("remind:demo-orders-miro")
        assert "Олег, добрый день" in app.session.sent[-1].text
        task = await app.send("Какие доступы нужны для ORD-118?")
        assert "Для задачи ORD-118" in task.text

        why = await app.send("Почему у нас Kafka, а не RabbitMQ?")
        assert "ответа нет" in why.text and "Пётр" in why.text
        await app.callback("esc:resp")
        assert "Вопрос от Даниил" in app.session.sent[-2].text  # note to the addressee (demo chat)
        assert "Передал вопрос" in app.session.sent[-1].text
        answer = callback_of(app.session.sent, "ans:")
        await app.callback(answer)
        assert "Напишите ответ" in app.session.sent[-1].text
        await app.send("Нужна шина событий между сервисами.")
        assert "ответил(а)" in app.session.sent[-2].text
        assert "Сохранить его в FAQ" in app.session.sent[-1].text
        await app.callback(callback_of(app.session.sent, "faq:"))
        assert "Сохранено в FAQ" in app.session.sent[-1].text
        again = await app.send("Зачем нам Kafka?")
        assert "Ответ из FAQ команды" in again.text
        await app.callback(callback_of([again], "rate:bot:"))
        assert "Спасибо" in app.session.sent[-1].text
        await app.callback(callback_of(app.session.sent, "rate:q-"))
        assert "Спасибо" in app.session.sent[-1].text
        await app.callback("nofaq:anything")
        assert "не сохраняю" in app.session.sent[-1].text
        await app.callback("esc:defer")
        assert "Нет вопроса" in app.session.sent[-1].text
        assert "только наставнику" in (await app.send("/interns")).text
        stage = await app.send("На каком этапе проект?")
        assert "ORD-100" in stage.text and stage.text.endswith("https://example.invalid/jira/browse/ORD-100")
    finally:
        await app.bot.session.close()


async def test_st3_live_1_bound_profiles_monitor_and_unreachable_addressee():
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="demo-daniil",
        display_name="Даниил",
        role="intern",
        team_id="demo-orders",
        mentor_id="demo-anna",
    )
    app = Harness(demo=False, identity=identity)
    controller = DemoController(True)
    controller.command(7)
    fixtures = controller.runtime(7)
    app.service.live = Runtime(data=SeedData(), gateway=fixtures.gateway, telegram=fixtures.telegram)
    try:
        status = await app.send("/access")
        assert "Ваши доступы" in status.text and not status.text.startswith("Демо")
        assert app.service.chats == {"demo-daniil": 42}
        assert await app.service.monitor_all() == []
        why = await app.send("Почему у нас Kafka, а не RabbitMQ?")
        assert "ответа нет" in why.text
        await app.callback("esc:resp")
        assert "не открывал бота" in app.session.sent[-1].text
        question = next(iter(app.service.live.data.questions))
        identity.profile.return_value = Profile(
            user_id="demo-petr", display_name="Пётр", role="mentor", team_id="demo-orders"
        )
        await app.callback("ans:" + question, user=43)
        await app.send("Нужна шина событий.", user=43)
        assert "Даниил" not in app.session.sent[-1].text  # answer goes to the author chat (42)
        assert app.session.sent[-2].chat_id == 42 and "ответил(а)" in app.session.sent[-2].text
        await app.callback("faq:" + question, user=43)
        assert "Сохранено" in app.session.sent[-1].text
        identity.profile.return_value = Profile(
            user_id="demo-anna", display_name="Анна", role="mentor", team_id="demo-orders"
        )
        assert "Доступы стажёров" in (await app.send("/interns", user=44)).text
    finally:
        await app.bot.session.close()


async def test_st3_live_1_without_runtime_flows_degrade_safely():
    identity = AsyncMock()
    identity.profile.return_value = Profile(
        user_id="demo-daniil", display_name="Даниил", role="intern", team_id="demo-orders"
    )
    app = Harness(demo=False, identity=identity)
    try:
        for data, expected in [
            ("mark:x", "недоступны"),
            ("remind:x", "недоступны"),
            ("esc:resp", "недоступна"),
            ("ans:q", "недоступны"),
            ("faq:q", "недоступен"),
            ("rate:bot:x:1", "Спасибо"),
        ]:
            await app.callback(data)
            assert expected in app.session.sent[-1].text
        assert await app.service.monitor_all() == []
        app.service.awaiting[42] = ("q-1", "demo-petr", app.service.known["demo-daniil"])
        await app.send("ответ без runtime")
        assert "не открывал бота" in app.session.sent[-1].text  # no runtime: addressee is unreachable
    finally:
        await app.bot.session.close()
