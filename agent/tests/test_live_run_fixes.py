"""Live run 12.11 fixes (reports/LIVE_RUN_BUGS.md): RT-03 refusal text, FR-H0-13 brief date."""

from dataclasses import replace
from datetime import UTC, datetime

from buddy.brief.service import BriefService
from buddy.demo.controller import DemoController
from buddy.executors.core import OUT_OF_SCOPE, OUT_OF_SCOPE_HR
from buddy.llm.client import FakeLLM
from buddy.router.service import Router
from buddy.service import AgentService


def demo_service(llm=None):
    service = AgentService(Router(llm), demo_mode=True)
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_rt_03_general_refusal_off_topic():
    """B-04: «рецепт борща» gets the general refusal without salary and HR BP."""
    service, context = demo_service()
    decision, response = await service.handle("напиши рецепт борща", context)
    assert decision.case == "out_of_scope" and response.result == OUT_OF_SCOPE
    assert "HR BP" not in response.result and "зарплат" not in response.result


async def test_rt_03_salary_and_personal_data_go_to_hr_bp():
    service, context = demo_service()
    questions = (
        "Какая у меня будет зарплата?",
        "Куда отправить паспорт?",
        "Мне поставили диагноз, что дальше?",
    )
    for text in questions:
        _, response = await service.handle(text, context)
        assert response.result == OUT_OF_SCOPE_HR, text


async def test_rt_03_refusal_text_is_chosen_by_code():
    service, context = demo_service(FakeLLM('{"case": "out_of_scope"}'))
    _, general = await service.handle("Сочини стих про понедельник", context)
    _, salary = await service.handle("Сколько премия у стажёра?", context)
    assert general.result == OUT_OF_SCOPE and salary.result == OUT_OF_SCOPE_HR


def demo_brief():
    controller = DemoController(True)
    controller.command(1)
    return controller.runtime(1), controller.context(1)


async def test_fr_h0_13_demo_header_marks_synthetic_day():
    """B-05: the demo day is fixed, the header says so."""
    runtime, context = demo_brief()
    response = await BriefService().now_brief(context, runtime)
    assert response.result.startswith("Бриф на 11.11 (учебный день демо):")


async def test_fr_h0_13_live_header_uses_current_date_in_user_timezone():
    runtime, context = demo_brief()
    runtime.brief_now = None
    runtime.clock = lambda: datetime(2026, 11, 11, 21, 30, tzinfo=UTC)  # 12.11 00:30 in Moscow

    class LiveBrief(BriefService):
        async def collect(self, context, runtime, sources):  # fixtures stand in for the live MCP servers
            return await super().collect(replace(context, demo=True), runtime, sources)

    response = await LiveBrief().now_brief(replace(context, demo=False), runtime)
    assert "учебный день" not in response.result
    assert response.result.startswith("Бриф на 12.11:")
