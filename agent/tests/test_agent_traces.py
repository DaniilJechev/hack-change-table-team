"""FR-TR-01: every graph turn is sent to the backend in the background; a failure never changes the answer."""

import asyncio
import json
import logging

import httpx

from buddy.data.traces import TraceSink
from buddy.llm.client import FakeLLM
from buddy.router.service import Router
from buddy.service import AgentService


def sink_with(handler):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return TraceSink("http://backend:8080", "service-key", transport=httpx.MockTransport(wrapped)), seen


def demo_service(sink):
    service = AgentService(Router(FakeLLM()), demo_mode=True, llm=FakeLLM(), traces=sink)
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_fr_tr_01_turn_is_written_with_user_team_and_path():
    sink, seen = sink_with(lambda request: httpx.Response(201, json={"id": "x"}))
    service, context = demo_service(sink)
    decision, response = await service.handle("/brief", context)
    await sink.close()
    assert response.result and len(seen) == 1
    request = seen[0]
    body = json.loads(request.content)
    assert request.url.path == "/api/v1/agent-traces" and request.headers["X-Buddy-Key"] == "service-key"
    assert "X-Buddy-User" not in request.headers
    assert body["user_id"] == context.user_id and body["team_id"] == context.team_id
    assert body["demo"] is True and body["path"] == "router" and body["case"] == decision.case
    assert body["id"].startswith("tr-") and body["turn"]["answer"]["text"] == response.result
    assert body["llm_calls"] == len(body["turn"]["other_llm_calls"]) and body["tool_calls"] == 0
    assert body["fallback"] is False


async def test_fr_tr_01_trigger_turn_is_written_too():
    sink, seen = sink_with(lambda request: httpx.Response(201, json={"id": "x"}))
    service, context = demo_service(sink)
    await service.trigger("brief_render", context)
    await sink.close()
    assert json.loads(seen[0].content)["path"] == "trigger"


async def test_fr_tr_01_unreachable_backend_keeps_the_answer_and_logs_the_kind(caplog):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    sink, _ = sink_with(down)
    service, context = demo_service(sink)
    with caplog.at_level(logging.WARNING, logger="buddy.data.traces"):
        _, response = await service.handle("/brief", context)
        await sink.close()
    assert response.result
    assert [record.getMessage() for record in caplog.records] == ["agent trace not saved: ConnectError"]


async def test_fr_tr_01_rejected_trace_logs_only_the_status(caplog):
    sink, _ = sink_with(lambda request: httpx.Response(400, json={"error": {}}))
    with caplog.at_level(logging.WARNING, logger="buddy.data.traces"):
        service, context = demo_service(sink)
        await service.handle("/brief", context)
        await sink.close()
    assert [record.getMessage() for record in caplog.records] == ["agent trace not saved: http 400"]


async def test_fr_tr_01_send_does_not_wait_for_the_backend():
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return httpx.Response(201, json={"id": "x"})

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await slow(request)

    sink = TraceSink("http://backend:8080", "k", transport=Slow())
    service, context = demo_service(sink)
    _, response = await asyncio.wait_for(service.handle("/brief", context), timeout=5)
    assert response.result and len(sink.tasks) == 1
    gate.set()
    await sink.close()
    assert not sink.tasks


async def test_fr_tr_01_state_without_turn_is_not_sent():
    sink, seen = sink_with(lambda request: httpx.Response(201))
    sink.send({"turn": None})
    await sink.close()
    assert seen == []
