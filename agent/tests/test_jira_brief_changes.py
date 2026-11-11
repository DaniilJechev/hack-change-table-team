"""JIRA-BRIEF: pinned MCP response shapes, personal history, pagination and offline compatibility."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from buddy.brief import collectors, jira_changes
from buddy.brief.service import BriefService
from buddy.demo.controller import DemoController
from buddy.executors.context import Context
from buddy.mcp_hub.errors import SourceUnavailable

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)
LOGIN = "account-123"
CONTEXT = Context(user_id="u", team_id="orders", role="intern")
TARGET = {"project_key": "ORD"}


def issue(key="ORD-1", **extra):
    return {
        "key": key,
        "summary": "Synthetic task",
        "browse_url": f"https://jira.example/browse/{key}",
        "url": "https://jira.example/rest/api/2/issue/123",
        "assignee": {"account_id": LOGIN, "name": "Different Display Name"},
        "created": (NOW - timedelta(days=7)).isoformat(),
        "updated": NOW.isoformat(),
        "status": {"name": "In Progress"},
        "changelogs": [],
        "comments": [],
        **extra,
    }


def history(field, before="Before", after="After", at=NOW, **extra):
    return {
        "created": at.isoformat(),
        "items": [{"field": field, "from_string": before, "to_string": after, **extra}],
    }


def runtime(details, pages=None):
    searches = iter(pages or [{"issues": [{"key": key} for key in details], "total": len(details)}])

    async def call(case, server, tool, arguments, *, user_id):
        assert (case, server, user_id) == ("brief_now", "jira", "u")
        if tool == "jira_search":
            assert 'project = "ORD"' in arguments["jql"]
            assert 'assignee WAS "account-123"' in arguments["jql"]
            assert "updated >=" in arguments["jql"] and arguments["limit"] == 50
            value = next(searches)
        else:
            assert tool == "jira_get_issue" and arguments["update_history"] is False
            assert arguments["include"] == "changelog,comments"
            value = details[arguments["issue_key"]]
        if isinstance(value, Exception):
            raise value
        # FastMCP's wrapped JSON string is supported as well as ordinary TextContent.
        return {"structuredContent": {"result": json.dumps(value)}}

    return SimpleNamespace(gateway=SimpleNamespace(call=AsyncMock(side_effect=call)))


async def collect(rt):
    return await collectors.jira(CONTEXT, rt, TARGET, LOGIN, SINCE, NOW)


async def test_jira_brief_1_fields_comments_and_source_links():
    histories = [history(field) for field in jira_changes.CHANGES]
    rt = runtime(
        {
            "ORD-1": issue(
                changelogs=histories,
                comments=[
                    {
                        "id": "20",
                        "created": NOW.isoformat(),
                        "body": "Please check",
                        "author": {"account_id": "other", "name": "Colleague"},
                    }
                ],
            )
        }
    )
    result = await collect(rt)
    assert {item.type for item in result.items} == {
        "jira_status_changed",
        "jira_reassigned",
        "jira_due_changed",
        "jira_priority_changed",
        "jira_description_changed",
        "jira_comment",
    }
    for item in result.items:
        assert item.url.startswith("https://jira.example/browse/ORD-1")
        if item.type not in {"jira_description_changed", "jira_comment"}:
            assert (item.before, item.after) == ("Before", "After")
            assert "Before → After" in item.line()
        else:
            assert (item.before, item.after) == ("", "")
    comment = next(item for item in result.items if item.type == "jira_comment")
    assert comment.url.endswith("focusedCommentId=20")


async def test_jira_brief_1_reassigned_away_foreign_task_and_project():
    away = issue(
        assignee={"account_id": "other"},
        changelogs=[
            history("assignee", "You", "Other", from_id=LOGIN, to_id="other"),
        ],
    )
    foreign = issue(
        "ORD-2",
        assignee={"account_id": "other"},
        changelogs=[history("status")],
        comments=[
            {"id": "1", "created": NOW.isoformat(), "body": LOGIN, "author": {"account_id": "other"}},
            {"id": "2", "created": NOW.isoformat(), "body": "unrelated", "author": {"account_id": "other"}},
        ],
    )
    rt = runtime(
        {"ORD-1": away, "ORD-2": foreign},
        pages=[
            {
                "issues": [{"key": "ORD-1"}, {"key": "ORD-2"}, {"key": "OTHER-1"}],
                "total": 3,
            }
        ],
    )
    result = await collect(rt)
    assert [(item.related_task, item.type) for item in result.items] == [
        ("ORD-1", "jira_reassigned"),
        ("ORD-2", "jira_mention"),
    ]
    assert len(rt.gateway.call.await_args_list) == 3


async def test_jira_brief_1_exact_period_and_stable_deduplicated_ids():
    current = history("status")
    data = issue(
        changelogs=[
            history("priority", at=SINCE),
            history("assignee", at=NOW + timedelta(seconds=1)),
            current,
            current,
        ],
        comments=[
            {"created": SINCE.isoformat(), "body": LOGIN},
            {"created": (NOW + timedelta(seconds=1)).isoformat(), "body": LOGIN},
            {"created": NOW.isoformat(), "body": LOGIN, "author": {"account_id": LOGIN}},
        ],
    )
    one, two = await collect(runtime({"ORD-1": data})), await collect(runtime({"ORD-1": data}))
    assert len(one.items) == 1 and one.items == two.items
    assert one.items[0].type == "jira_status_changed"


@pytest.mark.parametrize("user", [{"account_id": LOGIN}, {"name": LOGIN}, {"email": LOGIN}])
async def test_jira_brief_1_identity_and_real_assignment_not_creation(user):
    data = issue(assignee=user, changelogs=[history("assignee", "Other", "You", to_id=LOGIN)])
    result = await collect(runtime({"ORD-1": data}))
    assert [item.type for item in result.items] == ["jira_assigned"]
    assert "назначена вам" in result.items[0].title


@pytest.mark.parametrize("paging", ["cloud", "server"])
async def test_jira_brief_2_pagination(paging):
    pages = (
        [
            {"issues": [{"key": "ORD-1"}], "total": -1, "next_page_token": "next"},
            {"issues": [{"key": "ORD-2"}], "total": -1},
        ]
        if paging == "cloud"
        else [
            {"issues": [{"key": "ORD-1"}], "total": 2},
            {"issues": [{"key": "ORD-2"}], "total": 2},
        ]
    )
    rt = runtime({"ORD-1": issue(), "ORD-2": issue("ORD-2")}, pages)
    assert {item.related_task for item in (await collect(rt)).items} == {"ORD-1", "ORD-2"}
    second_search = rt.gateway.call.await_args_list[2].args[3]
    assert second_search.get("page_token") == "next" if paging == "cloud" else second_search["start_at"] == 1


@pytest.mark.parametrize(
    "pages,details",
    [
        ([{"issues": [], "total": 1}], {}),
        ([{"issues": [{"key": "ORD-1"}], "next_page_token": "loop"}] * 2, {"ORD-1": issue()}),
        ([{"error": "unavailable"}], {}),
        (None, {"ORD-1": SourceUnavailable("down")}),
        (None, {"ORD-1": {"key": "ORD-1"}}),
    ],
)
async def test_jira_brief_2_incomplete_or_failed_response(pages, details):
    with pytest.raises(SourceUnavailable):
        await collect(runtime(details, pages))


async def test_jira_brief_2_failure_is_marked_unavailable_by_service():
    demo = DemoController(True)
    demo.command(1)
    rt = demo.runtime(1)
    rt.gateway = SimpleNamespace(call=AsyncMock(side_effect=SourceUnavailable("down")))
    _, missing, _ = await BriefService().collect(demo.context(1), rt, ("jira",))
    assert missing == ["jira"]


async def test_jira_brief_2_updated_fallback_and_missing_values():
    data = issue()
    result = await collect(runtime({"ORD-1": data}))
    assert [item.type for item in result.items] == ["jira_updated"]
    assert result.items[0].before == result.items[0].after == ""
    data["changelogs"] = [history("status", before=None)]
    result = await collect(runtime({"ORD-1": data}))
    assert result.items[0].type == "jira_status_changed" and result.items[0].before == ""


async def test_jira_brief_2_demo_keeps_fixture_calls():
    demo = DemoController(True)
    demo.command(1)
    rt = demo.runtime(1)
    rt.gateway.call = AsyncMock(wraps=rt.gateway.call)
    result, missing, _ = await BriefService().collect(demo.context(1), rt, ("jira",))
    assert not missing and any(item.type == "jira_mention" for item in result.items)
    assert [entry.args[2] for entry in rt.gateway.call.await_args_list] == ["jira_search"]
