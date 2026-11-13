"""H1 (stage 3): read-only access checks on synthetic fixtures."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from buddy.access.checker import AccessChecker, FixtureTelegram, contains, payload
from buddy.access.service import AccessService
from buddy.demo.controller import DATA, DemoController
from buddy.executors.context import Context
from buddy.mcp_hub.errors import SourceUnavailable, ToolDenied

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def demo(step: int = 0, user: int = 1):
    controller = DemoController(True)
    controller.command(user)
    for _ in range(step):
        controller.command(user, "next")
    runtime = controller.runtime(user)
    runtime.clock = lambda: NOW
    return controller, runtime, controller.context(user)


def statuses(response) -> dict[str, str]:
    return {line.split(" — ")[0][2:]: line[0] for line in response.result.splitlines()[1:-1]}


async def test_fr_h1_01_fr_h1_02_fr_h1_03_status_checks_every_tool_in_place():
    _, runtime, context = demo()
    response = await AccessService().status(context, runtime)
    lines = response.result.splitlines()
    assert lines[0] == "Ваши доступы:"
    assert len(lines) == 4  # title + missing Miro + forbidden object + read-only note
    # FR-UI-01: tools that are fine are folded when something needs attention.
    granted = response.folded[0]
    assert granted.startswith("Доступ есть (4):")
    assert "✅ GitHub" in granted and "✅ Jira" in granted
    assert "⬜ Miro" in response.result and "Олег" in response.result
    assert "✅ Google Calendar" in granted and "✅ Telegram" in granted
    assert "⛔ GitHub — Закрытая конфигурация production" in response.result
    assert "ничего не меняет" in lines[-1]
    labels = [action.label for action in response.actions]
    assert any(label.startswith("Где запросить") for label in labels)
    assert any(action.data == "request:demo-orders-miro" for action in response.actions)
    assert all(link.startswith("https://example.invalid/") for link in response.links)
    assert "https" not in response.result  # links are appended by code, not inside executor text


async def test_fr_h1_04_mark_requested_then_stale_reminder_draft():
    _, runtime, context = demo()
    service = AccessService()
    marked = await service.mark(context, runtime, "demo-orders-miro")
    assert "Отметил" in marked.result and "Доска планирования" in marked.result
    again = await service.mark(context, runtime, "demo-orders-miro")
    assert again.result == marked.result  # original request date is kept
    assert "⏳ Miro" in (await service.status(context, runtime)).result
    runtime.clock = lambda: datetime.now(UTC) + timedelta(hours=49)
    stale = await service.status(context, runtime)
    assert "⏰ Miro" in stale.result and "дн." in stale.result
    assert any(action.data == "remind:demo-orders-miro" for action in stale.actions)
    draft = await service.reminder(context, runtime, "demo-orders-miro")
    assert "Олег, добрый день" in draft.result and "отправьте сами" in draft.result


@pytest.mark.parametrize("object_id", ["demo-orders-production", "unknown-object"])
async def test_fr_h1_06_forbidden_or_unknown_objects_cannot_be_marked(object_id):
    _, runtime, context = demo()
    response = await AccessService().mark(context, runtime, object_id)
    assert "нельзя отметить" in response.result
    reminder = await AccessService().reminder(context, runtime, object_id)
    assert "не нужно" in reminder.result


async def test_fr_h1_05_task_analysis_shows_only_mentioned_objects():
    _, runtime, context = demo()
    response = await AccessService().task(context, runtime, "ord-118")
    assert response.result.startswith("Для задачи ORD-118 [1] нужны доступы:")  # FR-UI-03: the task link
    shown = "\n".join([response.result, *response.folded])  # FR-UI-01: granted tools are folded
    assert "GitHub — Репозиторий" in response.folded[0] and "Miro" in response.result
    assert "Google Calendar" in shown and "production" in response.result
    assert "Telegram" not in shown and "Jira — Проект" not in shown
    assert response.links[0] == "https://example.invalid/jira/browse/ORD-118"


async def test_fr_h1_05_task_analysis_all_granted_after_access_appears():
    _, runtime, context = demo(step=1)
    response = await AccessService().task(context, runtime, "ORD-118")
    assert "доступов хватает ✅" in response.result


async def test_fr_h1_05_task_without_key_or_unavailable_issue():
    _, runtime, context = demo()
    assert "ключ задачи" in (await AccessService().task(context, runtime, "")).result
    assert "Не удалось получить" in (await AccessService().task(context, runtime, "ORD-999")).result


async def test_fr_h1_07_monitor_reports_only_changes():
    controller, runtime, context = demo()
    service = AccessService()
    assert await service.monitor(context, runtime) is None  # baseline
    assert await service.monitor(context, runtime) is None  # nothing changed
    controller.command(1, "next")
    runtime = controller.runtime(1)
    change = await service.monitor(context, runtime)
    assert "Miro" in change.result and "появился ✅" in change.result
    assert change.links == ["https://example.invalid/miro/board/demo-board-orders"]
    snapshot = runtime.data.snapshots["demo-daniil"]
    assert {item["object_id"]: item["status"] for item in snapshot["objects"]}[
        "demo-orders-miro"
    ] == "granted"


async def test_fr_h1_07_stale_transition_is_announced():
    _, runtime, context = demo()
    service = AccessService()
    await runtime.data.mark_requested("demo-daniil", "demo-orders-miro")
    assert await service.monitor(context, runtime) is None
    runtime.clock = lambda: datetime.now(UTC) + timedelta(hours=49)
    change = await service.monitor(context, runtime)
    assert "больше 48 ч" in change.result
    assert change.actions[0].data == "remind:demo-orders-miro"


async def test_fr_h1_08_interns_summary_for_mentor_only():
    _, runtime, context = demo()
    service = AccessService()
    intern = await service.interns(context, runtime)
    assert "только наставнику" in intern.result
    mentor = Context("demo-anna", "demo-orders", "mentor", True, 1)
    assert "проверок ещё не было" in (await service.interns(mentor, runtime)).result
    await runtime.data.mark_requested("demo-daniil", "demo-orders-miro")
    await service.status(context, runtime)
    summary = (await service.interns(mentor, runtime)).result
    assert "Даниил — 4/5, ждёт: demo-orders-miro" in summary
    lead = Context("demo-oleg", "demo-orders", "teamlead", True, 1)
    assert "Даниил" in (await service.interns(lead, runtime)).result
    petr = Context("demo-petr", "demo-orders", "mentor", True, 1)
    assert "пока нет" in (await service.interns(petr, runtime)).result


async def test_fr_h1_09_write_tools_are_never_reachable():
    _, runtime, context = demo()
    checker = AccessChecker(runtime.gateway, runtime.telegram, runtime.data.target)
    profile = await runtime.data.profile("demo-daniil")
    github = {"id": "demo-orders-github", "instrument": "github"}
    with pytest.raises(ToolDenied):
        await checker.membership("team_q1", github, profile)  # case allow-list blocks collaborators
    with pytest.raises(ToolDenied):
        await runtime.gateway.call("access_status", "miro", "board_share", {}, user_id="demo-daniil")


async def test_fr_h1_03_unknown_when_target_login_or_source_missing():
    _, runtime, context = demo()
    profile = await runtime.data.profile("demo-daniil")
    gateway = AsyncMock()
    gateway.call.side_effect = SourceUnavailable("down")
    checker = AccessChecker(gateway, runtime.telegram, runtime.data.target)
    catalog = await runtime.data.access_catalog("demo-daniil")
    items = await checker.check("access_status", profile, catalog, {}, NOW)
    by_id = {item.object_id: item for item in items}
    assert by_id["demo-orders-github"].status == "unknown"
    assert by_id["demo-orders-github"].error == "source_unavailable"
    assert by_id["demo-orders-telegram"].status == "granted"
    no_login = {**profile, "logins": {}}
    assert await checker.membership("access_status", catalog[0], no_login) is None
    no_target = AccessChecker(gateway, runtime.telegram, lambda _: None)
    assert await no_target.membership("access_status", catalog[0], profile) is None
    telegram = next(item for item in catalog if item["instrument"] == "telegram")
    assert await no_target.membership("access_status", telegram, profile) is None
    bad_login = {**profile, "logins": {"telegram": "not-a-number"}}
    assert await checker.membership("access_status", telegram, bad_login) is None


async def test_fr_h1_03_telegram_fixture_must_match(tmp_path):
    telegram = FixtureTelegram(DATA / "fixtures", "step_1")
    assert await telegram.status(-1001234567890, 10001) == "member"
    with pytest.raises(Exception):
        await telegram.status(-1001234567890, 20002)
    with pytest.raises(Exception):
        await FixtureTelegram(tmp_path, "step_1").status(1, 1)
    _, runtime, context = demo()
    runtime.telegram = FixtureTelegram(tmp_path, "step_1")
    response = await AccessService().status(context, runtime)
    assert "❔ Telegram" in response.result


def test_fr_h1_03_payload_and_contains_helpers():
    assert payload({"structuredContent": {"a": 1}}) == {"a": 1}
    assert payload({"content": [{"type": "text", "text": "plain"}]}) == "plain"
    assert payload({"content": [{"type": "text", "text": "[1]"}, {"type": "text", "text": "2"}]}) == [[1], 2]
    assert contains({"users": [{"name": "Demo.Daniil"}]}, "demo.daniil")
    assert not contains({"notice": "demo.daniil и другие"}, "demo.daniil")
    assert not contains(42, "x")


async def test_h1_data_unavailable_is_reported_safely():
    _, runtime, context = demo()
    runtime.data.users.pop("demo-daniil")
    service = AccessService()
    for response in [
        await service.status(context, runtime),
        await service.mark(context, runtime, "demo-orders-miro"),
        await service.reminder(context, runtime, "demo-orders-miro"),
        await service.interns(context, runtime),
    ]:
        assert "недоступн" in response.result
    assert await service.monitor(context, runtime) is None
    other = Context("demo-kirill", "demo-payments", "intern", True, 1)
    assert "проверять" not in (await service.status(other, runtime)).result


async def test_fr_h1_02_unknown_status_offers_no_request_and_keeps_saved_status():
    _, runtime, context = demo()
    service = AccessService()
    await service.status(context, runtime)  # baseline: Miro missing
    runtime.gateway = AsyncMock()
    runtime.gateway.call.side_effect = SourceUnavailable("down")
    response = await service.status(context, runtime)
    assert "❔ Miro" in response.result
    assert not any(action.data == "request:demo-orders-miro" for action in response.actions)
    assert service.previous[AccessService.key(context)]["demo-orders-miro"] == "missing"


async def test_fr_h1_10_five_programmatic_read_checks_without_llm():
    _, runtime, context = demo()
    calls = []
    original = runtime.gateway.call

    async def spy(case, server, tool, arguments, *, user_id):
        calls.append((case, server, tool))
        return await original(case, server, tool, arguments, user_id=user_id)

    runtime.gateway.call = spy
    response = await AccessService().status(context, runtime)
    assert [tool for _, _, tool in calls] == [
        "list_repository_collaborators",
        "jira_search_assignable_users",
        "content_item_list_roles",
        "list-events",
    ]  # Telegram is checked through Bot API getChatMember, not MCP
    assert {case for case, _, _ in calls} == {"access_status"}
    assert len(response.links) >= 5
