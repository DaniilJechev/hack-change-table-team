"""MOCK: offline simulations and the early, stable REST adapter for H1/H4."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from buddy.data.backend import BackendData
from buddy.data.base import DataError
from buddy.data.seed import SeedData
from buddy.demo.controller import DemoController

USER = "demo-daniil"
OBJECT = "demo-orders-miro"
NOW = datetime(2026, 11, 12, 10, tzinfo=UTC)


@pytest.mark.parametrize("status", ["granted", "pending", "denied", "error"])
async def test_mock_access_1_four_outcomes_and_idempotency(status):
    data = SeedData(clock=lambda: NOW)
    data.mock_rules = {
        "access_rules": [
            {
                "team_id": "demo-orders",
                "object_id": OBJECT,
                "status": status,
                "reason": "Учебная причина",
                "error_code": "service_unavailable" if status == "error" else "",
            }
        ]
    }
    result = await data.request_access(USER, OBJECT)
    assert result["status"] == status and result["simulated"] is True
    assert (await data.request_access(USER, OBJECT))["id"] == result["id"]
    assert await data.access_requests("Emil_Mardanov") == []
    if status == "denied":
        assert result["reason"]
    if status == "error":
        assert result["error_code"] == "service_unavailable"
    result["status"] = "tampered"
    assert (await data.access_requests(USER))[0]["status"] == status


async def test_mock_access_1_hidden_and_forbidden_objects_never_create_request():
    data = SeedData()
    for user, obj, kind in [
        (USER, "demo-orders-production", "forbidden"),
        ("demo-kirill", OBJECT, "not_found"),
        (USER, "missing", "not_found"),
        ("missing", OBJECT, "unauthorized"),
    ]:
        with pytest.raises(DataError) as error:
            await data.request_access(user, obj)
        assert error.value.kind == kind
    assert data.mock_requests == {}


@pytest.mark.parametrize("status,reason", [("granted", ""), ("denied", "Нет учебного основания")])
async def test_mock_access_2_teamlead_resolution(status, reason):
    data = SeedData()
    record = await data.request_access(USER, OBJECT)
    for user in [USER, "demo-anna"]:
        with pytest.raises(DataError, match="forbidden"):
            await data.resolve_access_request(user, record["id"], status, reason)
    data.users["demo-petr"]["team_id"] = "other"
    data.users["demo-petr"]["role"] = "teamlead"
    with pytest.raises(DataError, match="not_found"):
        await data.resolve_access_request("demo-petr", record["id"], status, reason)
    resolved = await data.resolve_access_request("demo-oleg", record["id"], status, reason)
    assert resolved == await data.resolve_access_request("demo-oleg", record["id"], status, reason)
    assert (await data.access_requests(USER))[0]["status"] == status
    with pytest.raises(DataError, match="conflict"):
        await data.resolve_access_request("demo-oleg", record["id"], "denied", "Другое решение")
    with pytest.raises(DataError, match="invalid"):
        await data.resolve_access_request("demo-oleg", record["id"], "denied")


async def test_mock_progress_1_mark_read_idempotency_and_user_isolation():
    data = SeedData(clock=lambda: NOW)
    assert (await data.progress(USER))[0]["completed"] is False
    result = await data.mark_progress(USER, "equator_presentation")
    assert result["completed"] and result["completed_at"] == NOW.isoformat()
    data.clock = lambda: NOW + timedelta(days=1)
    assert await data.mark_progress(USER, "equator_presentation") == result
    assert (await data.progress("Emil_Mardanov"))[0]["completed"] is False
    assert (await data.mark_progress(USER, "equator_presentation", False))["completed_at"] is None
    with pytest.raises(DataError, match="not_found"):
        await data.mark_progress(USER, "unknown")
    with pytest.raises(DataError, match="invalid"):
        await data.mark_progress(USER, "equator_presentation", 1)


async def test_mock_demo_1_miro_next_reset_and_session_isolation():
    demo = DemoController(True)
    demo.command(1)
    first = demo.runtime(1).data
    assert (await first.request_access(USER, OBJECT))["status"] == "pending"
    demo.command(2)
    assert await demo.runtime(2).data.access_requests(USER) == []
    demo.command(1, "next")
    assert (await demo.runtime(1).data.access_requests(USER))[0]["status"] == "granted"
    demo.command(1, "reset")
    assert await demo.runtime(1).data.access_requests(USER) == []


async def test_mock_demo_1_missing_policy_returns_code_and_error_can_retry():
    data = SeedData()
    data.mock_rules = {}
    record = await data.request_access(USER, OBJECT)
    assert record["status"] == "error" and record["error_code"] == "service_unconfigured"
    data.mock_rules = {"access_rules": [{"team_id": "demo-orders", "object_id": OBJECT, "status": "granted"}]}
    retried = await data.request_access(USER, OBJECT)
    assert retried["id"] == record["id"] and retried["status"] == "granted"


async def test_mock_demo_1_rest_adapter_uses_actor_and_stable_paths():
    calls = []

    def handle(request):
        assert request.headers["X-Buddy-Key"] == "synthetic-key"
        assert request.headers["X-Buddy-User"] == USER
        calls.append((request.method, request.url.path, json.loads(request.content or b"null")))
        return httpx.Response(200, json={"items": [], "simulated": True})

    data = BackendData("http://backend", "synthetic-key", transport=httpx.MockTransport(handle))
    try:
        await data.request_access(USER, OBJECT)
        assert await data.access_requests(USER) == []
        await data.resolve_access_request(USER, "ar-1", "granted")
        assert await data.progress(USER) == []
        await data.mark_progress(USER, "equator_presentation")
    finally:
        await data.close()
    assert calls == [
        ("POST", "/api/v1/mock/access-requests", {"object_id": OBJECT}),
        ("GET", "/api/v1/mock/access-requests", None),
        ("PATCH", "/api/v1/mock/access-requests/ar-1", {"status": "granted", "reason": ""}),
        ("GET", "/api/v1/mock/progress", None),
        ("PUT", "/api/v1/mock/progress/equator_presentation", {"completed": True}),
    ]
