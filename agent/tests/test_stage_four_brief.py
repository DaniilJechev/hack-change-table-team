"""H0 (stage 4): morning brief on synthetic fixtures — collectors, ranking, links, render, signals."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from buddy.brief import collectors
from buddy.brief.models import BriefItem, valid_url
from buddy.brief.schedule import due_at
from buddy.brief.service import BriefService, main_action, rank
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry, format_response
from buddy.llm.client import FakeLLM
from buddy.mcp_hub.errors import SourceUnavailable
from buddy.router.service import Router

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=MSK)


def demo(steps: int = 0):
    controller = DemoController(True)
    controller.command(1)
    for _ in range(steps):
        controller.command(1, "next")
    return controller, controller.runtime(1), controller.context(1)


def item(
    number: int, priority: str, kind: str = "pr_comment", minutes: int = 0, url: str = "ok"
) -> BriefItem:
    return BriefItem(
        id=f"i-{number}",
        source="github",
        type=kind,
        title=f"PR #{number}",
        url=f"https://example.invalid/pull/{number}" if url == "ok" else url,
        priority_class=priority,
        created_at=NOW - timedelta(minutes=minutes),
    )


class Patched:
    """Gateway wrapper: replaces the decoded payload of one tool or fails one server."""

    def __init__(self, inner, *, fail: str = "", edit=None):
        self.inner, self.fail, self.edit = inner, fail, edit

    async def call(self, case, server, tool, arguments, *, user_id):
        if server == self.fail:
            raise SourceUnavailable("down")
        result = await self.inner.call(case, server, tool, arguments, user_id=user_id)
        if self.edit is not None:
            value = collectors.payload(result)
            changed = self.edit(server, tool, value)
            if changed is not None:
                return {"content": [{"type": "text", "text": json.dumps(changed, ensure_ascii=False)}]}
        return result


async def test_fr_h0_01_changes_from_git_and_jira_since_last_brief():
    _, runtime, context = demo()
    merged, missing, now = await BriefService().collect(context, runtime, ("github", "jira"))
    kinds = {entry.type for entry in merged.items}
    assert {"pr_changes_requested", "jira_mention", "pr_review_requested", "jira_assigned"} <= kinds
    assert missing == [] and now == NOW


async def test_fr_h0_01_calendar_move_and_cancel_from_snapshot_comparison():
    _, runtime, context = demo()
    merged, _, _ = await BriefService().collect(context, runtime, ("calendar",))
    moved = next(entry for entry in merged.items if "ORD-101" in entry.title)
    assert (moved.type, moved.before, moved.after) == ("meeting_moved", "15:00", "12:00")
    assert moved.related_task == "ORD-101"
    cancelled = next(entry for entry in merged.items if entry.type == "meeting_cancelled")
    assert "ретро" in cancelled.title and cancelled.before == "17:00"
    assert merged.calendar["events"]["demo-sync-ord-101"]["start"].startswith("2026-11-11T12:00")


async def test_fr_h0_01_first_run_without_snapshot_only_records_baseline_and_new_meeting():
    _, runtime, context = demo()
    target = runtime.data.target("demo-orders-google_calendar")
    first = await collectors.calendar(context, runtime, target, None, NOW)
    assert first.items == [] and len(first.calendar["events"]) == 2
    saved = {"events": {"demo-standup": first.calendar["events"]["demo-standup"]}}
    second = await collectors.calendar(context, runtime, target, saved, NOW)
    assert [entry.type for entry in second.items] == ["meeting_new"]
    other_day = {
        "events": {"demo-standup": {**saved["events"]["demo-standup"], "start": "2026-11-10T09:30:00+03:00"}}
    }
    assert (await collectors.calendar(context, runtime, target, other_day, NOW)).items == []


async def test_fr_h0_01_fr_h0_10_fr_h0_11_failed_brief_keeps_changes_and_sent_items_never_repeat():
    _, runtime, context = demo()
    service = BriefService()
    first = await service.now_brief(context, runtime)
    await service.delivered(first.brief_id, runtime, False)  # Telegram rejected the message
    assert await runtime.data.last_brief_sent(context.user_id) is None
    again = await service.now_brief(context, runtime)
    assert "запрошены изменения" in again.result and "перенесена" in again.result
    await service.delivered(again.brief_id, runtime, True)
    assert (await runtime.data.last_brief_sent(context.user_id)).startswith("2026-11-11T09:00")
    assert "Изменений нет" in (await service.now_brief(context, runtime)).result
    await service.delivered("br-unknown", runtime, True)  # unknown drafts are ignored


async def test_fr_h0_02_jira_mention_is_normalised_to_brief_item():
    _, runtime, context = demo()
    merged, _, _ = await BriefService().collect(context, runtime, ("jira",))
    mention = next(entry for entry in merged.items if entry.type == "jira_mention")
    assert (mention.source, mention.priority_class, mention.related_task) == ("jira", "people", "ORD-118")
    assert mention.url == "https://example.invalid/jira/browse/ORD-118?focusedCommentId=5001"
    assert set(mention.model_dump()) == {
        "id",
        "source",
        "type",
        "title",
        "url",
        "priority_class",
        "related_task",
        "before",
        "after",
        "created_at",
    }


def test_fr_h0_03_seven_changes_two_blockers_first_five_shown():
    items = [item(n, "info", minutes=n) for n in range(5)] + [
        item(5, "blocker", "pr_changes_requested"),
        item(6, "blocker", "pr_changes_requested", minutes=5),
    ]
    ranked = rank(items)
    assert [entry.id for entry in ranked[:2]] == ["i-5", "i-6"] and len(ranked[5:]) == 2
    order = [
        entry.priority_class for entry in rank([item(1, "info"), item(2, "deadline"), item(3, "people")])
    ]
    assert order == ["people", "deadline", "info"]


async def test_fr_h0_03_fr_h0_04_brief_has_five_items_more_button_and_main_action():
    _, runtime, context = demo()
    service = BriefService()
    response = await service.now_brief(context, runtime)
    lines = response.result.splitlines()
    assert lines[1].startswith("▶ Главное сегодня: исправить замечания в PR #45")
    assert lines[2].startswith("1. 🔴 Git: PR #45") and len(lines) == 7 and len(response.links) == 5
    assert response.actions[0].label == "Ещё 3" and response.actions[1].label == "✅ Прочитал"
    await service.delivered(response.brief_id, runtime, True)
    more = await service.signal("more", response.brief_id, context, runtime)
    assert more.result.splitlines()[1].startswith("6.") and len(more.links) == 3
    assert main_action([item(1, "info", "pr_approved")]) is None


async def test_fr_h0_link_1_item_links_point_to_the_concrete_object():
    _, runtime, context = demo()
    response = await BriefService().now_brief(context, runtime)
    assert response.links[0].endswith("/pull/45#pullrequestreview-9001")  # the review, not the PR list
    assert "https" not in response.result  # links are attached by code, outside the text


async def test_fr_h0_link_2_item_without_url_is_hidden_and_logged():
    _, runtime, context = demo()

    def strip(server, tool, value):
        if tool == "pull_request_read":
            for review in value["reviews"]:
                review["html_url"] = "not a link"
            return value
        if tool == "list_pull_requests":
            for pull in value["pull_requests"]:
                pull["html_url"] = ""
            return value
        return None

    runtime.gateway = Patched(runtime.gateway, edit=strip)
    service = BriefService()
    response = await service.now_brief(context, runtime, source="github")
    assert "Изменений по источнику Git нет" in response.result
    assert {entry["error"] for entry in service.errors} == {"invalid_url"} and len(service.errors) == 2
    assert not valid_url("") and not valid_url("https://example.invalid") and not valid_url("ftp://x/y")
    assert not valid_url("https://user:pw@example.invalid/x") and valid_url("https://example.invalid/x")


async def test_fr_h0_link_3_llm_rewords_by_item_id_and_never_touches_links():
    _, runtime, context = demo()
    plain = await BriefService().now_brief(context, runtime)
    ids = [entry.id for entry in next(iter(BriefService().drafts.values()), None).shown] if False else None
    service = BriefService()
    draft_ids = [
        entry.id
        for entry in rank((await service.collect(context, runtime, ("github", "jira", "calendar")))[0].items)
    ]
    good = {
        "main": "Поправить замечания Анны в PR #45",
        "lines": [{"item_id": draft_ids[0], "text": "Анна просит правки в PR #45"}]
        + [{"item_id": "i-unknown", "text": "Выдуманный пункт"}],
    }
    service.llm = FakeLLM("Вот бриф: " + json.dumps(good, ensure_ascii=False))
    worded = await service.now_brief(context, runtime)
    assert "▶ Главное сегодня: Поправить замечания Анны" in worded.result
    assert "1. Анна просит правки в PR #45" in worded.result and "Выдуманный" not in worded.result
    assert worded.links == plain.links and ids is None
    assert "item_id" in service.llm.calls[0] and "https://" not in service.llm.calls[0]


@pytest.mark.parametrize(
    "raw",
    [
        '{"main": "x", "lines": [{"item_id": "a", "text": "нет"}, {"item_id": "b", "text": "нет"}]}',
        "не JSON",
        '{"lines": []}',
        '{"lines": "строка"}',
    ],
)
async def test_fr_h0_link_3_untrusted_llm_answer_falls_back_to_template(raw):
    _, runtime, context = demo()
    service = BriefService(FakeLLM(raw))
    response = await service.now_brief(context, runtime)
    assert "1. 🔴 Git: PR #45" in response.result and "исправить замечания" in response.result


async def test_fr_h0_link_3_llm_text_with_url_is_rejected():
    _, runtime, context = demo()
    service = BriefService()
    first = rank((await service.collect(context, runtime, ("github",)))[0].items)[0].id
    answer = {
        "main": "см. https://evil.example/x",
        "lines": [{"item_id": first, "text": "см. http://evil.example"}],
    }
    service.llm = FakeLLM(json.dumps(answer))
    response = await service.now_brief(context, runtime, source="github")
    assert "evil" not in format_response(response) and "исправить замечания" in response.result


async def test_fr_h0_05_unavailable_source_is_noted_and_brief_still_sent():
    _, runtime, context = demo()
    runtime.gateway = Patched(runtime.gateway, fail="jira")
    response = await BriefService().now_brief(context, runtime)
    assert "Данные из Jira сейчас недоступны." in response.result
    assert "Git: PR #45" in response.result and "Календарь" in response.result
    assert not any("jira" in link for link in response.links)


async def test_fr_h0_05_unconfigured_source_and_data_errors_are_safe():
    _, runtime, context = demo()
    runtime.data.targets.pop("demo-orders-github")
    assert "Данные из Git сейчас недоступны." in (await BriefService().now_brief(context, runtime)).result
    runtime.data.users.pop("demo-daniil")
    service = BriefService()
    assert "недоступны" in (await service.now_brief(context, runtime)).result
    assert "недоступны" in (await service.stats(context, runtime)).result
    assert await service.scheduled(context, runtime) is None


def test_fr_h0_06_brief_time_is_30_minutes_before_first_standup():
    events = [
        {"summary": "Командный стендап", "start": {"dateTime": "2026-11-11T10:00:00+03:00"}},
        {"summary": "Daily вторая", "start": "2026-11-11T12:00:00+03:00"},
        {"summary": "Стендап отменён", "start": "2026-11-11T08:00:00+03:00", "status": "cancelled"},
    ]
    assert due_at(events, NOW, "09:30") == NOW.replace(hour=9, minute=30)
    assert due_at([], NOW, "09:45") == NOW.replace(hour=9, minute=45)  # no standup: default time
    assert due_at(events, NOW, "09:30", "08:15") == NOW.replace(hour=8, minute=15)  # /settings wins
    assert due_at([{"summary": "Стендап", "start": "завтра"}], NOW, "09:30") == NOW.replace(minute=30)


def test_fr_h0_06_no_brief_on_weekend_or_day_of_absence():
    saturday = datetime(2026, 11, 14, 9, 0, tzinfo=MSK)
    assert due_at([], saturday, "09:30") is None
    vacation = [{"summary": "Отпуск", "start": {"date": "2026-11-11"}}]
    assert due_at(vacation, NOW, "09:30") is None
    assert due_at([{"summary": "Отпуск коллеги: созвон", "start": "2026-11-11T11:00:00+03:00"}], NOW, "09:30")


async def test_fr_h0_06_scheduler_sends_once_when_time_has_come():
    _, runtime, context = demo()
    service = BriefService()
    assert await service.scheduled(context, runtime) is None  # 09:00, standup 10:30 → due 10:00
    runtime.brief_now = NOW.replace(hour=10, minute=0)
    sent = await service.scheduled(context, runtime)
    assert sent.result.startswith("☀️ Утренний бриф на 11.11") and sent.brief_id
    assert await service.scheduled(context, runtime) is None  # once per day
    runtime.gateway = Patched(runtime.gateway, fail="calendar")
    other = BriefService()
    runtime.brief_now = NOW.replace(hour=9, minute=31)
    assert (
        "Календаря сейчас недоступны" not in (await other.scheduled(context, runtime)).result
    )  # default 09:30
    weekend = BriefService()
    runtime.brief_now = datetime(2026, 11, 14, 12, 0, tzinfo=MSK)
    assert await weekend.scheduled(context, runtime) is None


async def test_fr_h0_07_single_source_request_and_no_changes_answer():
    _, runtime, context = demo()
    router, registry = Router(None), Registry()
    decision = await router.route("Есть что по PR?", context=context)
    assert (decision.case, decision.params) == ("brief_now", {"source": "github"})
    only_git = await registry.execute(decision.case, decision.params, context, runtime, "Есть что по PR?")
    assert "Git:" in only_git.result and "Jira" not in only_git.result and "Календарь" not in only_git.result
    assert only_git.brief_id is None  # a quick look is not the daily brief
    full = await registry.execute("brief_now", {}, context, runtime, "/brief")
    await registry.brief.delivered(full.brief_id, runtime, True)
    empty = await registry.execute("brief_now", {}, context, runtime, "Что нового?")
    assert (
        empty.result.startswith("Изменений нет.")
        and "Ближайшее: Учебный стендап — 11.11 10:30." in empty.result
    )
    assert empty.links == ["https://example.invalid/calendar/event/demo-standup"]
    assert (await router.route("/brief jira")).params == {"source": "jira"}
    assert (await router.route("Что нового в календаре и Jira?")).params == {}


async def test_fr_h0_08_first_signal_marks_brief_read_and_stats_show_shares():
    _, runtime, context = demo()
    service = BriefService()
    first = await service.now_brief(context, runtime)
    await service.delivered(first.brief_id, runtime, True)
    stats = (await service.stats(context, runtime)).result
    assert "отправлено 1, прочитано 0%" in stats  # no reaction: the brief stays unread
    assert "Отметил" in (await service.signal("read", first.brief_id, context, runtime)).result
    assert "Спасибо" in (await service.signal("positive", first.brief_id, context, runtime)).result
    record = await runtime.data.brief(context.user_id, first.brief_id)
    assert record["read_at"] and [event["kind"] for event in record["events"]] == ["read", "positive"]
    await runtime.data.brief_event(context.user_id, first.brief_id, "click", record["items"][0]["id"])
    stats = (await service.stats(context, runtime)).result
    assert "прочитано 100% (цель ≥ 60%), с переходами 100%, 👍 1 / 👎 0" in stats
    with pytest.raises(DataError):
        await runtime.data.brief_event(context.user_id, first.brief_id, "click", "i-unknown")
    assert "Не понял" in (await service.signal("delete", first.brief_id, context, runtime)).result
    assert "недоступен" in (await service.signal("read", "br-missing", context, runtime)).result


async def test_fr_h0_08_reply_is_a_read_signal_and_offline_links_remain_direct():
    controller, runtime, context = demo(2)  # next morning
    settings = type(
        "S",
        (),
        {
            "brief_timezone": "Europe/Moscow",
            "redirect_base_url": "https://buddy.example/",
            "brief_time": "09:30",
        },
    )
    service = BriefService(settings=settings)
    response = await service.now_brief(context, runtime)
    assert "срок сегодня" in response.result and "одобрен" in response.result
    assert all(not link.startswith("https://buddy.example/r/") for link in response.links)
    await service.delivered(response.brief_id, runtime, True)
    await service.replied(response.brief_id, context, runtime)
    assert (await runtime.data.brief(context.user_id, response.brief_id))["events"][0]["kind"] == "reply"
    mentor = DemoController(True)
    mentor.command(2, "mentor")
    assert "брифов ещё не было" in (await service.stats(mentor.context(2), mentor.runtime(2))).result
    runtime.data.brief_event = AsyncMock(side_effect=DataError("unavailable"))
    assert "Не удалось" in (await service.signal("read", response.brief_id, context, runtime)).result


async def test_fr_h0_09_settings_time_and_quiet_mode():
    _, runtime, context = demo()
    service = BriefService()
    assert "выключен" in (await service.settings(context, runtime, "")).result
    assert "08:45" in (await service.settings(context, runtime, "brief 08:45")).result
    assert "включён" in (await service.settings(context, runtime, "quiet on")).result
    assert "Настройки:" in (await service.settings(context, runtime, "brief 25:00")).result
    first = await service.scheduled(context, runtime)  # 09:00 ≥ custom 08:45
    await service.delivered(first.brief_id, runtime, True)
    runtime.brief_now = NOW + timedelta(days=1)
    quiet = BriefService()
    runtime.gateway = Patched(
        runtime.gateway, edit=lambda server, tool, value: {} if server != "calendar" else None
    )
    assert await quiet.scheduled(context, runtime) is None  # quiet mode: no changes → no message


async def test_fr_h0_11_save_failure_keeps_brief_usable_without_read_buttons():
    _, runtime, context = demo()
    runtime.data.save_brief = AsyncMock(side_effect=DataError("unavailable"))
    service = BriefService()
    response = await service.now_brief(context, runtime)
    assert response.brief_id is None and [action.label for action in response.actions] == ["Ещё 3"]
    more = response.actions[0].data.rsplit(":", 1)[1]
    assert "Остальные" in (await service.signal("more", more, context, runtime)).result
    runtime.data.brief_status = AsyncMock(side_effect=DataError("unavailable"))
    ok = BriefService()
    runtime.data.save_brief = AsyncMock(return_value=None)
    saved = await ok.now_brief(context, runtime)
    await ok.delivered(saved.brief_id, runtime, True)  # status error is logged, not raised


def test_h0_collector_helpers_tolerate_foreign_shapes():
    assert collectors.moment({"dateTime": "2026-11-11T10:00:00Z"}) == datetime(2026, 11, 11, 10, tzinfo=UTC)
    assert collectors.moment("2026-11-11T10:00:00") is None and collectors.moment(None) is None
    assert collectors.login_of({"email": "A@Example.invalid"}) == "a@example.invalid"
    assert collectors.rows("text") == [] and collectors.rows([{"a": 1}, 2]) == [{"a": 1}]
    assert collectors.snapshot_of(
        [{"id": "x", "start": "2026-11-11T10:00:00+03:00", "status": "cancelled"}]
    ) == {"events": {}}
    assert collectors.task_key("без ключа") == ""


async def test_h0_jira_overdue_and_bad_dates_and_github_comment_review():
    _, runtime, context = demo()

    def edit(server, tool, value):
        if tool == "jira_search":
            value["issues"][0]["duedate"] = "2026-11-09"
            value["issues"][1]["duedate"] = "когда-нибудь"
            return value
        if tool == "pull_request_read":
            value["reviews"][0]["state"] = "COMMENTED"
            return value
        return None

    runtime.gateway = Patched(runtime.gateway, edit=edit)
    merged, _, _ = await BriefService().collect(context, runtime, ("github", "jira"))
    titles = [entry.title for entry in merged.items]
    assert any("срок прошёл" in title for title in titles) and any(
        "новый комментарий" in title for title in titles
    )
