from dataclasses import replace

import pytest
from test_stage_two_bot import Harness

from buddy.access.checker import member_in_response
from buddy.data.seed import SeedData
from buddy.executors.core import format_response
from buddy.router.service import Router
from buddy.service import AgentService


@pytest.mark.parametrize(
    "instrument,value,expected",
    [
        ("github", {"description": "demo-user", "collaborators": []}, False),
        ("github", {"collaborators": [{"login": "DEMO-USER"}]}, True),
        ("github", {"collaborators": "demo-user"}, None),
        ("github", {"collaborators": ["demo-user"]}, None),
        ("jira", {"users": [{"name": "demo-user", "active": False}]}, False),
        ("jira", {"users": [{"accountId": "demo-user"}]}, True),
        ("miro", {"roles": [{"description": "demo-user"}]}, False),
        ("miro", {"roles": [{"user_id": "demo-user", "role": "viewer"}]}, True),
        ("google_calendar", {"events": [{"organizer": {"email": "demo-user"}, "attendees": []}]}, False),
        ("google_calendar", {"events": [{"attendees": [{"email": "demo-user"}]}]}, True),
        (
            "google_calendar",
            {"events": [{"status": "cancelled", "attendees": [{"email": "demo-user"}]}]},
            False,
        ),
        ("google_calendar", {"events": [{"attendees": "broken"}]}, None),
    ],
)
def test_fr_h1_11_only_participant_fields_confirm_access(instrument, value, expected):
    assert member_in_response(instrument, value, "demo-user") is expected


async def test_fr_h2_10_faq_keeps_specific_source_after_consent():
    data = SeedData()
    await data.create_question("demo-daniil", "q-source", "Почему Kafka?", "demo-petr")
    await data.answer_question("demo-petr", "q-source", "Учебная шина событий")
    await data.publish_faq("demo-petr", "f-source", "q-source")
    service = AgentService(Router(), demo_mode=True)
    service.demo.command(1)
    runtime = service.demo.runtime(1)
    runtime.data = data
    response = await service.executors.team.answer(
        "team_q1", {}, "Почему Kafka?", service.demo.context(1), runtime
    )
    assert response.links == ["https://example.invalid/team/demo-orders/faq/f-source"]
    assert response.links[0] in format_response(response)


async def test_fr_h1_01_access_links_reference_repo_project_and_board_once():
    app = Harness()
    try:
        await app.send("/demo")
        response = await app.send("/access")
        assert 'href="https://example.invalid/github/demo-buddy/orders-onboarding"' in response.text
        assert 'href="https://example.invalid/jira/projects/ORD"' in response.text
        assert "/pull/45" not in response.text and "/browse/ORD-118" not in response.text
        board = await app.send("Где доска планирования?")
        assert "Miro" in board.text
        assert board.text.count("https://example.invalid/miro/board/demo-board-orders") == 1
    finally:
        await app.bot.session.close()


async def test_fr_h2_10_demo_reset_clears_pending_answer_and_monitor_baseline():
    app = Harness()
    try:
        await app.send("/demo")
        await app.send("/access")
        await app.send("Почему у нас Kafka, а не RabbitMQ?")
        await app.callback("esc:resp")
        data = app.service.demo.runtime(42).data
        question = next(iter(data.questions))
        await app.callback("ans:" + question)
        assert app.service.is_answering(42)
        await app.send("/demo reset")
        assert not app.service.is_answering(42)
        assert "demo-daniil:42" not in app.service.executors.access.previous
        assert "demo-daniil:42" not in app.service.executors.team.pending
        assert not app.service.demo.runtime(42).data.questions
        result = await app.send("Кто мой наставник?")
        assert "Анна" in result.text and "ответил(а)" not in result.text
    finally:
        await app.bot.session.close()


async def test_fr_h2_10_answers_require_addressee_and_original_context():
    service = AgentService(Router(), demo_mode=True)
    service.demo.command(1)
    context = service.demo.context(1)
    runtime = service.demo.runtime(1)
    await runtime.data.create_question("demo-daniil", "q-context", "Почему Kafka?", "demo-petr")
    result = await service.start_answer("q-context", context, 1)
    assert "Напишите ответ" in result.result
    changed = replace(context, team_id="other-team")
    result = await service.submit_answer("не отправлять", changed, 1)
    assert "Контекст изменился" in result[0][1].result
    assert not runtime.data.questions["q-context"]["answer"]
    # Outside demo roleplay the author cannot start answering their own question.
    service.live = runtime
    author = replace(context, demo=False, session=None)
    denied = await service.start_answer("q-context", author, 1)
    assert "только адресат" in denied.result
    assert not service.is_answering(1)
