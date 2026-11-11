"""Stage 4 integration contracts: opaque links, persisted callbacks and metadata-only audit."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from buddy.brief.service import BriefService
from buddy.data.backend import BackendData
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry
from buddy.mcp_hub.errors import ToolDenied


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller.runtime(1), controller.context(1)


async def test_st4_redirect_1_opaque_backend_tokens_and_restored_callbacks():
    runtime, context = demo()
    original = runtime.data.save_brief

    async def save(user_id, brief_id, items):
        await original(user_id, brief_id, items)
        tokens = {item["id"]: f"{number:064x}" for number, item in enumerate(items, 1)}
        runtime.data._brief(user_id, brief_id)["redirect_tokens"] = tokens
        return {"redirect_tokens": tokens}

    runtime.data.save_brief = save
    settings = SimpleNamespace(redirect_base_url="https://buddy.example/")
    first = BriefService(settings=settings)
    response = await first.now_brief(context, runtime)
    assert len(response.links) == 5
    assert all(
        link.startswith("https://buddy.example/r/") and len(link.rsplit("/", 1)[1]) == 64
        for link in response.links
    )
    await first.delivered(response.brief_id, runtime, True)
    restored = BriefService(settings=settings)
    more = await restored.signal("more", response.brief_id, context, runtime)
    assert more.links and all("/r/" in link for link in more.links)
    assert "прочитан" in (await restored.signal("read", response.brief_id, context, runtime)).result
    await restored.replied(response.brief_id, context, runtime)
    record = await runtime.data.brief(context.user_id, response.brief_id)
    assert [event["kind"] for event in record["events"]] == ["read", "read", "reply"]


async def test_st4_metrics_1_hub_saves_success_and_denial_without_arguments():
    runtime, context = demo()
    sink = AsyncMock()
    runtime.gateway.audit_sink = sink
    await runtime.gateway.call(
        "team_q5", "jira", "jira_get_issue", {"issue_key": "ORD-118"}, user_id=context.user_id
    )
    with pytest.raises(ToolDenied):
        await runtime.gateway.call(
            "team_q5", "jira", "jira_create_issue", {"description": "private"}, user_id=context.user_id
        )
    records = [call.args[0] for call in sink.await_args_list]
    assert [record["outcome"] for record in records] == ["ok", "tool_denied"]
    assert all(set(record) == {"case", "server", "tool", "user_id", "outcome"} for record in records)
    assert "private" not in json.dumps(records) and "ORD-118" not in json.dumps(records)
    runtime.gateway.audit_sink = AsyncMock(side_effect=RuntimeError("storage down"))
    # A telemetry failure must not turn a successful read into a failed user operation.
    assert await runtime.gateway.call(
        "team_q5", "jira", "jira_get_issue", {"issue_key": "ORD-118"}, user_id=context.user_id
    )


async def test_st4_metrics_1_backend_audit_and_h3_attribution():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        assert request.headers["X-Buddy-User"] == "u"
        calls.append((request.url.path, body))
        return httpx.Response(200, json={"id": "q"})

    async def mcp(user, tool, arguments):
        return {"user_id": user, "role": "intern"}

    data = BackendData("http://backend", "synthetic", transport=httpx.MockTransport(handler), mcp=mcp)
    assert (await data.profile("u"))["user_id"] == "u"
    assert calls[0][0] == "/api/v1/mcp-calls" and calls[0][1]["outcome"] == "ok"
    await data.record_bot_answer("u", "q", "Question", "Answer", ["https://example.org/source"], "h3")
    assert calls[1][1]["capability"] == "h3"
    data._rest = AsyncMock(side_effect=DataError("unavailable"))
    await data.record_mcp_call(
        {
            "user_id": "u",
            "case": "brief_now",
            "server": "jira",
            "tool": "jira_search",
            "outcome": "source_unavailable",
        }
    )
    await data.close()


async def test_st4_metrics_1_playbook_ratings_keep_h3_category():
    runtime, context = demo()
    registry = Registry()
    response = await registry.execute("playbook_qa", {}, context, runtime, "Что такое Zero Bug Policy?")
    question_id = response.actions[0].data.split(":")[2]
    record = await runtime.data.question(context.user_id, question_id)
    assert record["capability"] == "h3"
    await runtime.data.rate_bot_answer(context.user_id, question_id, True)
    assert (await runtime.data.question(context.user_id, question_id))["bot_helpful"] is True


async def test_st4_metrics_1_cancelled_sdk_call_is_not_logged_as_success():
    data = BackendData("http://backend", "synthetic", mcp=AsyncMock(side_effect=asyncio.CancelledError()))
    data.record_mcp_call = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await data.profile("u")
    assert data.record_mcp_call.await_args.args[0]["outcome"] == "cancelled"
    await data.close()
