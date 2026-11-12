"""FR-H1-13: actual dispatcher → own mock data, no external writes or invented grants."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from tests.test_stage_three_bot import buttons
from tests.test_stage_two_bot import Harness

from buddy.access.service import AccessService
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.mcp_hub.errors import SourceUnavailable

USER = "demo-daniil"
OBJECT = "demo-orders-miro"


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller, controller.runtime(1), controller.context(1)


async def test_fr_h1_04_new_button_request_and_old_mark_remains_manual():
    app = Harness()
    try:
        await app.send("/demo")
        status = await app.send("/access")
        assert any(button.callback_data == "request:" + OBJECT for button in buttons(status))
        assert not any(button.text.startswith("Я запросил") for button in buttons(status))
        await app.callback("request:" + OBJECT)
        assert "на согласовании" in app.session.sent[-1].text
        assert "имитация" in app.session.sent[-1].text
        data = app.service.demo.runtime(42).data
        assert len(await data.access_requests(USER)) == 1
        await app.callback("request:" + OBJECT)
        assert len(await data.access_requests(USER)) == 1
        await app.send("/demo reset")
        await app.callback("mark:" + OBJECT)
        assert "Отметил" in app.session.sent[-1].text
        assert await app.service.demo.runtime(42).data.access_requests(USER) == []
    finally:
        await app.bot.session.close()


@pytest.mark.parametrize(
    "status,expected",
    [
        ("granted", "доступ выдан"),
        ("pending", "на согласовании"),
        ("denied", "Учебная причина"),
        ("error", "service_unavailable"),
    ],
)
async def test_fr_h1_13_four_saved_outcomes(status, expected):
    _, rt, context = demo()
    rt.data.mock_rules = {
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
    service = AccessService()
    response = await service.request(context, rt, OBJECT)
    assert expected in response.result and "имитация" in response.result
    assert (await rt.data.access_requests(USER))[0]["status"] == status
    shown = await service.status(context, rt)
    assert expected in shown.result
    if status == "granted":
        assert "Реальные права проверяются отдельно" in response.result
        assert "⬜ Miro" in shown.result  # simulation is not actual Miro membership
    if status in {"denied", "error", "granted"}:
        rt.clock = lambda: datetime.now(UTC) + timedelta(days=3)
        later = await service.status(context, rt)
        assert "⏰ Miro" not in later.result and "⏳ Miro" not in later.result


@pytest.mark.parametrize("code", ["service_unconfigured", "rate_limited", "untrusted-secret"])
async def test_fr_h1_13_safe_error_templates(code):
    _, rt, context = demo()
    rt.data.request_access = AsyncMock(return_value={"status": "error", "error_code": code})
    response = await AccessService().request(context, rt, OBJECT)
    assert "Код:" in response.result
    assert "untrusted-secret" not in response.result


async def test_fr_h1_13_backend_failure_does_not_report_success():
    _, rt, context = demo()
    rt.data.request_access = AsyncMock(side_effect=DataError("unavailable"))
    response = await AccessService().request(context, rt, OBJECT)
    assert "Код: unavailable" in response.result and "доступ выдан" not in response.result
    for kind in ("not_found", "forbidden"):
        rt.data.request_access.side_effect = DataError(kind)
        assert "нельзя запросить" in (await AccessService().request(context, rt, OBJECT)).result


async def test_fr_h1_13_forbidden_hidden_unknown_and_already_granted_never_request():
    _, rt, context = demo()
    rt.data.request_access = AsyncMock()
    service = AccessService()
    for obj in ("demo-orders-production", "foreign-object"):
        assert "нельзя запросить" in (await service.request(context, rt, obj)).result
    assert "уже подтверждён" in (await service.request(context, rt, "demo-orders-github")).result
    rt.gateway.call = AsyncMock(side_effect=SourceUnavailable("down"))
    assert "не отправлял" in (await service.request(context, rt, OBJECT)).result
    rt.data.request_access.assert_not_called()


async def test_fr_h1_13_demo_miro_transition_and_external_calls_are_read_only():
    controller, rt, context = demo()
    service = AccessService()
    await service.status(context, rt)
    await service.request(context, rt, OBJECT)
    controller.command(1, "next")
    rt = controller.runtime(1)
    response = await service.monitor(context, rt)
    assert "появился ✅" in response.result
    assert (await rt.data.access_requests(USER))[0]["status"] == "granted"
    assert all(entry["outcome"] != "denied" for entry in rt.gateway.audit)


async def test_fr_h1_13_no_runtime_safe_callback():
    app = Harness(demo=False)
    try:
        # The wrapper itself remains safe when no live backend runtime is configured.
        from buddy.executors.context import Context

        response = await app.service.request_access(OBJECT, Context(USER, "demo-orders", "intern"))
        assert "недоступны" in response.result
    finally:
        await app.bot.session.close()
