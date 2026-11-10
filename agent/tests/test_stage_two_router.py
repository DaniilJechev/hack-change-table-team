import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from buddy.executors.core import Context
from buddy.llm.client import FakeLLM
from buddy.router.rules import decide
from buddy.router.service import CASES, Router, parse
from buddy.service import AgentService

ROOT = Path(__file__).resolve().parents[2]
ROWS = [
    json.loads(line) for line in (ROOT / "evals/router/cases.jsonl").read_text(encoding="utf-8").splitlines()
]
CONTROL = [
    json.loads(line)
    for line in (ROOT / "evals/router/holdout.jsonl").read_text(encoding="utf-8").splitlines()
]


@pytest.mark.parametrize("row", ROWS + CONTROL, ids=lambda row: row["id"])
async def test_rt_01_rt_03_rt_07_all_cases_and_parameters(row):
    result = await Router().route(row["text"])
    assert result.case == row["case"]
    assert all(result.params.get(key) == value for key, value in row["params"].items())


async def test_st2_data_1_dataset_and_reproducible_report():
    assert len(ROWS) == 70 and len(CONTROL) == 16
    assert set(row["case"] for row in ROWS) == set(CASES)
    spec = importlib.util.spec_from_file_location("router_evals", ROOT / "evals/run_router.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.load(ROOT / "evals/router/cases.jsonl") == ROWS
    for rows, filename in [(ROWS, "rules-report.json"), (CONTROL, "holdout-report.json")]:
        report = await module.evaluate(Router(), rows)
        committed = json.loads((ROOT / "evals/router" / filename).read_text(encoding="utf-8"))
        assert all(committed[key] == value for key, value in report.items())
        assert report["accuracy"] == 1 and report["parameter_errors"] == 0


async def test_st2_route_1_valid_model_no_confidence_threshold():
    model = FakeLLM('{"case":"team_q5","params":{}}')
    result = await Router(model).route("Непривычная формулировка")
    assert result.case == "team_q5" and len(model.calls) == 1
    assert "confidence" in model.calls[0]  # prompt forbids a confidence field
    assert parse('{"case":"write_code","params":{"role":"teamlead"}}').case == "out_of_scope"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"case":"help","confidence":0.99}',
        '{"case":"team_q1","params":{"team_id":"payments"}}',
        '{"case":"help","params":{"user_id":"other"}}',
        '{"case":"help","params":{"role":"teamlead"}}',
        '{"case":"help","params":{"menu":42}}',
    ],
)
async def test_st2_route_1_malformed_or_privileged_model_uses_rules(raw):
    assert (await Router(FakeLLM(raw)).route("Упал staging, кому писать?")) == decide(
        "Упал staging, кому писать?"
    )


async def test_rt_07_failure_timeout_commands_and_cancellation():
    llm = AsyncMock()
    llm.complete.side_effect = RuntimeError("hidden-model-secret")
    assert (await Router(llm).route("Что такое Zero Bug Policy?")).case == "playbook_qa"
    llm.complete.reset_mock()
    assert (await Router(llm).route("/ask")).params == {"menu": "team"}
    assert (await Router(llm).route("/unknown")).case == "out_of_scope"
    assert (await Router(llm).route("x" * 4001)).case == "clarify"
    assert (await Router(llm).route("")).case == "clarify"
    llm.complete.assert_not_called()

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    llm.complete.side_effect = slow
    assert (await Router(llm, timeout=0.01).route("Где доска?")).case == "team_q8"
    llm.complete.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await Router(llm).route("Вопрос")
    for timeout in (0, 21):
        with pytest.raises(ValueError):
            Router(timeout=timeout)


async def test_rt_02_unknown_case_executes_out_of_scope():
    service = AgentService(Router(FakeLLM('{"case":"write_code"}')), demo_mode=True)
    service.demo.command(1)
    decision, response = await service.handle("Напиши приложение", service.demo.context(1))
    assert decision.case == "out_of_scope" and "наставник" in response.result


@pytest.mark.parametrize("case", ["brief_render", "access_events", "team_q1"])
async def test_rt_04_direct_triggers_never_call_router(case):
    router = AsyncMock()
    service = AgentService(router, demo_mode=True)
    response = await service.trigger(case, Context("u", "team", "mentor"))
    assert response.result
    router.route.assert_not_called()


async def test_st2_route_1_trusted_context_is_separate_from_text():
    model = FakeLLM('{"case":"team_q1"}')
    result = await Router(model).route(
        "Кто отвечает за Kafka?", context=Context("private-user", "payments", "mentor")
    )
    assert result.case == "team_q1"
    assert '"team_id": "payments"' in model.calls[0] and '"role": "mentor"' in model.calls[0]
    assert "private-user" not in model.calls[0]
    huge = json.dumps({"case": "team_q1", "params": {"area": "a" * 4001}})
    assert (await Router(FakeLLM(huge)).route("/help")).case == "help"
    assert (await Router(FakeLLM(huge)).route("Кто отвечает за Kafka?")).case == "team_q1"
