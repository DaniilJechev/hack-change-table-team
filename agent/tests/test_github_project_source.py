"""GH-SOURCE: live-shaped read-only responses, repository scoping and demo isolation."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from buddy.access.checker import AccessChecker, member_in_response
from buddy.access.service import AccessService
from buddy.brief import collectors
from buddy.data.backend import BackendData
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.executors.context import Context
from buddy.mcp_hub.errors import SourceUnavailable
from buddy.team.service import TeamService

REPO = {"owner": "DaniilJechev", "repo": "hack-change-table-team"}
URL = "https://github.com/DaniilJechev/hack-change-table-team"
NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


async def test_gh_source_2_personal_live_brief_with_real_mcp_shapes():
    calls = []

    class Gateway:
        async def call(self, case, server, tool, arguments, *, user_id):
            calls.append((tool, arguments))
            assert arguments["owner"] == REPO["owner"] and arguments["repo"] == REPO["repo"]
            if tool == "list_pull_requests":
                assert arguments["state"] == "all" and arguments["perPage"] == 100
                return {
                    "structuredContent": [
                        {
                            "number": 1,
                            "title": "Own",
                            "user": {"login": "synthetic-user"},
                            "state": "closed",
                            "html_url": URL + "/pull/1",
                            "updated_at": NOW.isoformat(),
                        },
                        {
                            "number": 2,
                            "title": "Needs review",
                            "user": {"login": "other"},
                            "state": "open",
                            "requested_reviewers": ["synthetic-user"],
                            "html_url": URL + "/pull/2",
                            "updated_at": NOW.isoformat(),
                        },
                        {
                            "number": 3,
                            "title": "Unrelated",
                            "user": {"login": "other"},
                            "state": "open",
                            "html_url": URL + "/pull/3",
                            "updated_at": NOW.isoformat(),
                        },
                    ]
                }
            assert arguments["pullNumber"] == 1
            method = arguments["method"]
            if method == "get_reviews":
                return {
                    "structuredContent": [
                        {
                            "id": 9,
                            "state": "APPROVED",
                            "user": {"login": "reviewer"},
                            "submitted_at": NOW.isoformat(),
                            "html_url": URL + "/pull/1#review",
                        }
                    ]
                }
            if method == "get_comments":
                return {
                    "structuredContent": [
                        {
                            "user": {"login": "reviewer"},
                            "created_at": NOW.isoformat(),
                            "html_url": URL + "/pull/1#issuecomment-1",
                        },
                        {
                            "user": {"login": "synthetic-user"},
                            "created_at": NOW.isoformat(),
                            "html_url": URL + "/pull/1#self",
                        },
                    ]
                }
            assert method == "get_review_comments"
            return {
                "structuredContent": {
                    "review_threads": [
                        {
                            "comments": [
                                {
                                    "author": "reviewer",
                                    "created_at": NOW.isoformat(),
                                    "html_url": URL + "/pull/1#discussion_r1",
                                }
                            ]
                        }
                    ]
                }
            }

    context = Context(user_id="u", team_id="orders", role="intern")
    result = await collectors.github(
        context, SimpleNamespace(gateway=Gateway()), REPO, "synthetic-user", NOW - timedelta(days=1)
    )
    assert {item.type for item in result.items} == {"pr_review_requested", "pr_approved", "pr_comment"}
    assert len(result.items) == 4 and all(item.url.startswith(URL + "/pull/") for item in result.items)
    assert len(calls) == 4


async def test_gh_source_3_collaborators_pagination_and_unknown_on_failure():
    profile = {"user_id": "u", "logins": {"github": "synthetic-user"}}
    obj = {"id": "repo", "instrument": "github", "target": REPO}
    gateway = SimpleNamespace(
        call=AsyncMock(
            side_effect=[
                {"structuredContent": {"items": [{"login": "other"}], "nextPage": 2}},
                {"structuredContent": {"items": [{"login": "Synthetic-User"}], "nextPage": 0}},
            ]
        )
    )
    checker = AccessChecker(gateway, None, lambda _: None)
    assert await checker.membership("access_status", obj, profile) is True
    assert gateway.call.await_args.args[3]["page"] == 2
    gateway.call = AsyncMock(
        side_effect=[{"structuredContent": {"items": [], "nextPage": 2}}, SourceUnavailable("down")]
    )
    with pytest.raises(SourceUnavailable):
        await checker.membership("access_status", obj, profile)
    gateway.call = AsyncMock(return_value={"structuredContent": {"items": [], "nextPage": 1}})
    assert await checker.membership("access_status", obj, profile) is None
    assert member_in_response("github", {"items": []}, "synthetic-user") is False


async def test_gh_source_2_live_catalog_link_and_error_are_not_demo_link():
    controller = DemoController(True)
    controller.command(1)
    runtime, context = controller.runtime(1), controller.context(1)
    runtime.data.access_catalog = AsyncMock(
        return_value=[
            {
                "id": "repo",
                "instrument": "github",
                "url": URL,
                "target": REPO,
            }
        ]
    )
    service = TeamService(AccessService())
    service.access._items = AsyncMock(return_value=[])
    card = (await runtime.data.team_card(context.user_id))["document"]
    response = await service._team_q8({"object": "github"}, "", card, context, runtime)
    assert response.links == [URL]
    listing = await service._team_q8({}, "", card, context, runtime)
    assert URL in listing.links and not any("/github/" in link for link in listing.links)
    runtime.data.access_catalog.side_effect = DataError("unavailable")
    response = await service._team_q8({"object": "github"}, "", card, context, runtime)
    assert response.links == [] and "недоступны" in response.result


async def test_gh_source_3_rest_info_and_calendar_roundtrip():
    saved = {}

    def handler(request):
        if request.method == "POST":
            saved.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "b", "items": saved["items"]})

    data = BackendData("http://backend", "synthetic", transport=httpx.MockTransport(handler))
    await data.save_brief("u", "b", [{"id": "i", "source": "calendar", "priority_class": "info"}])
    assert saved["items"][0]["source"] == "google_calendar"
    assert saved["items"][0]["priority_class"] == "information"
    item = (await data.brief("u", "b"))["items"][0]
    assert item["source"] == "calendar" and item["priority_class"] == "info"
    await data.close()
