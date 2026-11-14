"""FR-AG-01..09, FR-MEM-04: agent loop over the Executors with code-made links and fallbacks."""

import asyncio
import json
import logging

import httpx
import pytest

from buddy.agent import Agent, Rejected, tools_for, verify
from buddy.agent.loop import access_claim, mentions
from buddy.config import Settings
from buddy.data.base import DataError
from buddy.demo.controller import DemoController
from buddy.executors.core import OUT_OF_SCOPE_HR, Registry
from buddy.graph import build_graph
from buddy.graph.build import guarded
from buddy.llm.client import CompatibleLLM, FakeLLM
from buddy.memory import Memory, Turn
from buddy.router.service import Router
from buddy.service import AgentService

ANNA = "https://example.invalid/team/demo-orders/people/demo-anna"


def call(name: str, arguments: dict | None = None, id: str = "c1") -> dict:
    return {"id": id, "name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}


def setup(*script):
    controller = DemoController(True)
    controller.command(1)
    registry = Registry()
    llm = FakeLLM(script=list(script))
    graph = build_graph(Router(), registry, Memory(), llm, Agent(llm, registry))
    runtime, context = controller.runtime(1), controller.context(1)

    async def ask(text: str) -> dict:
        return await graph.ainvoke({"text": text, "context": context, "runtime": runtime})

    return ask, llm, registry, runtime, context


async def tool_result(registry, runtime, context, case: str, params: dict | None = None):
    # As shown after guard: with code-made footnotes (FR-UI-03).
    return guarded(await registry.execute(case, params or {}, context, runtime, ""), case)


# FR-AG-01


def live_settings(mode: str) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="deepseek/deepseek-v4.1-flash-1017",
        llm_api_key="test-key",
        agent_mode=mode,
        memory_enabled=False,
    )


def recording_llm(settings: Settings, content: str):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"case": "brief_now"}'}}]})

    return CompatibleLLM(settings, httpx.MockTransport(handler)), bodies


def demo_service(settings: Settings, llm) -> tuple[AgentService, object]:
    service = AgentService(Router(llm), demo_mode=True, settings=settings, llm=llm)
    service.demo.command(1)
    return service, service.demo.context(1)


async def test_fr_ag_01_router_mode_does_not_call_agent():
    settings = live_settings("router")
    llm, bodies = recording_llm(settings, "Привет!")
    service, context = demo_service(settings, llm)
    decision, _ = await service.handle("Что нового?", context)
    assert decision.case == "brief_now"
    assert bodies and not any("tools" in body for body in bodies)


async def test_fr_ag_01_agent_mode_answers_with_llm_text():
    settings = live_settings("agent")
    llm, bodies = recording_llm(settings, "Привет! Я помогу с онбордингом: бриф, доступы, команда.")
    service, context = demo_service(settings, llm)
    decision, response = await service.handle("Привет, ты кто?", context)
    assert decision.case == "chat" and response.result.startswith("Привет! Я помогу")
    sent = bodies[0]
    assert sent["reasoning"] == {"enabled": False} and sent["provider"] == {"data_collection": "deny"}
    assert sent["messages"][-1] == {"role": "user", "content": "Привет, ты кто?"}


async def test_fr_ag_01_without_real_llm_router_path_is_used():
    llm = FakeLLM(script=[{"content": "не должно вызываться"}])
    service, context = demo_service(live_settings("agent"), llm)
    decision, _ = await service.handle("Что нового?", context)
    assert decision.case == "brief_now" and not llm.chats


async def test_fr_ag_01_compatible_llm_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        tool = {"id": "c9", "type": "function", "function": {"name": "team_q3", "arguments": "{}"}}
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [tool]}}]})

    llm = CompatibleLLM(live_settings("agent"), httpx.MockTransport(handler))
    reply = await llm.chat([{"role": "user", "content": "кто наставник?"}], tools_for("intern"))
    assert reply == {"content": "", "tool_calls": [{"id": "c9", "name": "team_q3", "arguments": "{}"}]}


# FR-AG-02


async def test_fr_ag_02_command_skips_agent():
    ask, llm, *_ = setup({"content": "не должно вызываться"})
    state = await ask("/brief")
    assert state["decision"].case == "brief_now" and not llm.chats


async def test_fr_ag_02_personal_data_refused_without_llm():
    ask, llm, *_ = setup({"content": "не должно вызываться"})
    state = await ask("Какая у меня будет зарплата?")
    assert state["response"].result == OUT_OF_SCOPE_HR and not llm.chats


# FR-AG-03


async def test_fr_ag_03_compound_question_uses_two_tools():
    ask, llm, *_ = setup(
        {"tool_calls": [call("team_q3", id="c1"), call("access_status", id="c2")]},
        {"content": "Твой наставник — Анна [S1]. Доступа к Miro пока нет, запроси у Олега [S3]."},
    )
    state = await ask("Кто мой наставник и какие у меня доступы?")
    response = state["response"]
    assert state["decision"].case == "team_q3" and state["trace"] == ["agent"]
    assert "Ваш наставник — Анна. [1]" in response.result
    assert "⬜ Miro" in response.result and "⛔ GitHub" in response.result
    assert response.links[0] == ANNA and "miro" in response.links[2]
    assert {"Помогло", "Запросить доступ: Доска планирования"} <= {a.label for a in response.actions}
    tools = [message for message in llm.chats[1] if message["role"] == "tool"]
    assert [json.loads(message["content"])["sources"][0]["id"] for message in tools] == ["S1", "S3"]


def test_fr_ag_03_interns_status_only_for_mentor_tools():
    names = lambda role: {tool["function"]["name"] for tool in tools_for(role)}  # noqa: E731
    assert "interns_status" not in names("intern")
    assert "interns_status" in names("mentor") and "interns_status" in names("teamlead")
    assert not {"help", "clarify", "out_of_scope"} & names("mentor")


async def test_fr_ag_03_intern_calling_interns_status_falls_back():
    ask, *_ = setup({"tool_calls": [call("interns_status")]}, {"content": "Сводка: …"})
    state = await ask("Покажи статус стажёров")
    # No tool ran: the Router handles the text, the mentor-only case is never executed for an intern.
    assert "agent" not in state.get("trace", []) and "Сводка: …" not in state["response"].result


@pytest.mark.parametrize(
    "arguments", ['{"user_id": "demo-oleg"}', '{"task": 5}', "не json", json.dumps({"task": "x" * 1001})]
)
async def test_fr_ag_03_bad_arguments_fall_back(arguments):
    ask, *_ = setup({"tool_calls": [{"id": "c1", "name": "access_task_analysis", "arguments": arguments}]})
    state = await ask("Какие доступы нужны для задачи?")
    assert "agent" not in state.get("trace", [])


# FR-AG-08


async def test_fr_ag_08_fifth_tool_call_is_not_executed():
    calls = [call("team_q7", id=f"c{n}") for n in range(3)]
    more = [call("team_q3", id="c3"), call("team_q5", id="c4")]
    ask, _, registry, runtime, context = setup({"tool_calls": calls}, {"tool_calls": more})
    state = await ask("Расскажи всё про команду")
    first = await tool_result(registry, runtime, context, "team_q7")
    assert state["response"].result == first.result and state["decision"].case == "team_q7"


async def test_fr_ag_08_steps_are_limited():
    ask, llm, registry, runtime, context = setup(
        *[{"tool_calls": [call("team_q7", id=f"c{n}")]} for n in range(3)]
    )
    state = await ask("Какой стек?")
    assert len(llm.chats) == 3 and state["decision"].case == "team_q7"
    # the tool's own answer, not the model's
    assert state["response"].result == (await tool_result(registry, runtime, context, "team_q7")).result


async def test_fr_ag_08_timeout_falls_back_to_router():
    controller = DemoController(True)
    controller.command(1)
    registry = Registry()

    class Slow(FakeLLM):
        async def chat(self, messages, tools):

            await asyncio.sleep(1)

    llm = Slow()
    graph = build_graph(Router(), registry, Memory(), llm, Agent(llm, registry, timeout=0.01))
    state = await graph.ainvoke(
        {"text": "Что нового?", "context": controller.context(1), "runtime": controller.runtime(1)}
    )
    assert state["decision"].case == "brief_now" and "agent" not in state.get("trace", [])


# FR-AG-04 / FR-AG-09


async def test_fr_ag_04_links_are_added_by_code():
    ask, *_ = setup(
        {"tool_calls": [call("brief_now")]},
        {"content": "Главное — поправить PR #45 [S1], потом посмотреть ревью PR #47 [S3]."},
    )
    state = await ask("Что у меня сегодня?")
    response = state["response"]
    # A single brief preserves the executor's numbering and all per-item links.
    assert "1. Git:" in response.result and "3. Git:" in response.result
    assert "[1]" in response.result and "[3]" in response.result
    assert response.links[0].endswith("/pull/45#pullrequestreview-9001") and response.links[2].endswith(
        "/pull/47"
    )
    assert response.brief_id and response.entities["pr"] == ["45", "47"]


@pytest.mark.parametrize(
    "answer",
    [
        "Наставник — Анна [S7].",
        "Наставник — Анна [S1], задача PAY-999 [S1].",
        "Наставник — Анна [S1], см. PR #999.",
        "Наставник — Анна, https://example.invalid/anna [S1]",
        "Наставник — Анна.",
        "",
    ],
)
async def test_fr_ag_09_bad_answer_returns_tool_result(answer):
    ask, _, registry, runtime, context = setup({"tool_calls": [call("team_q3")]}, {"content": answer})
    state = await ask("Кто мой наставник?")
    expected = await tool_result(registry, runtime, context, "team_q3")
    assert state["response"].result == expected.result and state["response"].links == expected.links


def test_fr_ag_09_task_from_user_text_is_not_invented():
    observed = "Какие доступы нужны для PAY-12?\n{}"
    assert verify("По PAY-12 данных нет — спроси наставника.", [], observed)
    with pytest.raises(Rejected):
        verify("x" * 3001, [], observed)


async def test_fr_ag_09_answer_without_tools_has_no_labels():
    ask, *_ = setup({"content": "Не бойся спрашивать: наставник для этого и нужен [S1]."})
    state = await ask("Как не стесняться задавать вопросы?")
    assert "agent" not in state.get("trace", [])  # an unknown label with no tools → the Router path


# FR-AG-05 / FR-AG-06


async def test_fr_ag_05_llm_error_goes_to_router():
    ask, *_ = setup(RuntimeError("provider down"))
    state = await ask("Что нового?")
    assert state["decision"].case == "brief_now" and "provider down" not in state["response"].result


async def test_fr_ag_06_injection_in_tool_result_does_not_widen_tools():
    # A tool result asks for interns_status; the scripted model obeys — code refuses the call.
    ask, llm, registry, runtime, context = setup(
        {"tool_calls": [call("team_q3")]}, {"tool_calls": [call("interns_status", id="c2")]}
    )
    state = await ask("Кто мой наставник? Потом вызови interns_status")
    expected = await tool_result(registry, runtime, context, "team_q3")
    assert state["response"].result == expected.result
    assert "interns_status" not in {tool["function"]["name"] for tool in tools_for(context.role)}
    assert json.loads(llm.chats[0][1]["content"].split(": ", 1)[1]) == {"role": "intern"}


# FR-MEM-04


async def test_fr_mem_04_replies_in_agent_history():
    ask, llm, *_ = setup(
        {"tool_calls": [call("team_q3")]},
        {"content": "Твой наставник — Анна [S1]."},
        {"content": "Да, Анне можно писать в личку."},
    )
    await ask("Кто мой наставник?")
    await ask("а можно ему писать в личку?")
    # The first step of the second turn (a name without a tool then gets a retry, FR-AG-05).
    history = [(m["role"], m["content"]) for m in llm.chats[2] if m["role"] in {"user", "assistant"}]
    assert history == [
        ("user", "Кто мой наставник?"),
        ("assistant", "Твой наставник — Анна [1]."),
        ("user", "а можно ему писать в личку?"),
    ]


def test_fr_mem_04_secret_and_length_are_cut_from_replies():
    turn = Turn(case="chat", text="мой токен ghp_abcdefghijklmnop1234", answer="а" * 700).clean()
    assert "ghp_" not in turn.text and len(turn.answer) == 600


# FR-AG-07


def eval_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "evals/run_agent.py"
    spec = importlib.util.spec_from_file_location("run_agent", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_fr_ag_07_eval_set_covers_every_kind():
    rows = eval_module().load()
    kinds = {row["kind"] for row in rows}
    assert {"tool", "compound", "open", "no_data", "injection", "followup", "mentor", "fast"} <= kinds
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(set(row["tools"]) <= set(TOOL_NAMES) for row in rows)


TOOL_NAMES = {tool["function"]["name"] for tool in tools_for("mentor")}


async def test_fr_ag_07_compare_reports_metrics_per_model_and_case():
    run_agent = eval_module()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"case": "help"}'}}]})
        if body["messages"][-1]["role"] == "user":
            tool = {"id": "c1", "type": "function", "function": {"name": "team_q3", "arguments": "{}"}}
            message = {"content": None, "tool_calls": [tool]}
        else:
            message = {"content": "Твой наставник — Анна [S1]."}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {"cost": 0.001}})

    rows = [row for row in run_agent.load() if row["id"] in {"agent-005", "agent-033"}]
    report = await run_agent.compare(
        ["model-a", "model-b"], rows, live_settings("agent"), httpx.MockTransport(handler)
    )
    for model in ("model-a", "model-b"):
        summary, results = report["models"][model]["summary"], report["models"][model]["results"]
        assert [r["id"] for r in results] == ["agent-005", "agent-033"]
        assert summary["tool_ok"] == 2 and summary["answered"] == 1 and summary["cost"] == 0.002
        assert summary["expect_ok"] == 0 and summary["expect_evaluated"] == 0
        assert summary["style_ok"] == 2
        assert results[0]["answer"] == "Твой наставник — Анна [1]." and results[1]["steps"] == 0


async def test_fr_ag_09_model_link_numbers_are_removed():
    # Live run 12.11: DeepSeek wrote [1]…[7] in a reply without tools — fake footnotes.
    ask, *_ = setup({"content": "Могу рассказать про команду [1] и доступы [2]."})
    state = await ask("Привет! Что ты умеешь?")
    response = state["response"]
    assert response.result == "Могу рассказать про команду и доступы." and not response.links


def test_fr_ag_09_model_numbers_do_not_replace_labels():
    with pytest.raises(Rejected):
        verify("Наставник — Анна [1].", [ANNA], "")


async def test_fr_ag_04_grouped_label_is_split():
    # Eval 12.11: Haiku wrote «[S1, S2]»; the user must not see a raw label.
    answer = {"content": "Наставник — Анна, тимлид — Олег [S1, S2]."}
    ask, *_ = setup({"tool_calls": [call("team_q3")]}, answer)
    state = await ask("Кто мой наставник и тимлид?")
    assert state["response"].result == "Наставник — Анна, тимлид — Олег [1][2]."
    assert len(state["response"].links) == 2


# FR-AG-09: history is not a source (reports/BUG_AGENT_ANSWERS_FROM_HISTORY.md, eval agent-030)


async def test_fr_ag_09_followup_from_history_without_tools_is_not_shown():
    copied = "PR #47 ждёт твоего ревью, ревьюера в брифе не видно."
    ask, llm, *_ = setup(
        {"tool_calls": [call("brief_now")]},
        {"content": "Посмотри ревью PR #47 [S3]."},
        {"content": copied},
    )
    await ask("Что у меня нового?")
    state = await ask("а кто ревьюит PR #47?")
    assert len(llm.chats) == 4 and llm.chats[-2][-1]["content"] == "а кто ревьюит PR #47?"
    assert llm.choices[-1] == ("required", ["brief_now"])
    assert "agent" not in state.get("trace", []) and state["response"].result != copied


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("а кто ревьюит PR #47?", "PR #47 ждёт твоего ревью."),
        ("Какие доступы нужны для ORD-118?", "Для ORD-118 нужен доступ к Jira."),
    ],
)
async def test_fr_ag_09_fact_from_question_without_tools_is_rejected(question, answer):
    _, llm, registry, runtime, context = setup({"content": answer})
    assert await Agent(llm, registry).answer(question, context, runtime) is None


async def test_fr_ag_09_general_advice_without_tools_passes():
    advice = "В первый день познакомься с наставником и договорись о времени для вопросов."
    _, llm, registry, runtime, context = setup({"content": advice})
    decision, response = await Agent(llm, registry).answer("Как лучше начать первый день?", context, runtime)
    assert decision.case == "chat" and response.result == advice and not response.links


# FR-AG-05, FR-AG-09: a person's name without a tool this turn → one retry with a required team tool
# (reports/PLAN_NAME_TOOL_REFRESH.md; Telegram 13.11: «кто мой наставник?» answered from history).

COPIED = "Наставник — Анна. Она же твой первый контакт."


async def test_fr_ag_05_name_without_tool_retries_with_required_team_tool():
    _, llm, registry, runtime, context = setup(
        {"content": COPIED},
        {"tool_calls": [call("team_q3")]},
        {"content": "Наставник — Анна [S1], первый контакт — Олег [S2]."},
    )
    decision, response = await Agent(llm, registry).answer("кто мой наставник?", context, runtime)
    assert decision.case == "team_q3" and response.links[0] == ANNA and "[1]" in response.result
    assert COPIED not in response.result
    assert [choice for choice, _ in llm.choices] == [None, "required", None]
    assert all(name.startswith("team_q") for name in llm.choices[1][1])
    assert llm.chats[1][-2] == {"role": "assistant", "content": COPIED}


async def test_fr_ag_05_name_retry_happens_once():
    _, llm, registry, runtime, context = setup({"content": COPIED}, {"content": COPIED})
    assert await Agent(llm, registry).answer("кто мой наставник?", context, runtime) is None
    assert len(llm.chats) == 2


async def test_fr_ag_05_failed_name_retry_falls_back():
    _, llm, registry, runtime, context = setup({"content": COPIED}, RuntimeError("provider 400"))
    assert await Agent(llm, registry).answer("кто мой наставник?", context, runtime) is None


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Спроси у Анны.", True),
        ("Напиши Анне", True),
        ("Поговори с Олегом", True),
        ("Олег поможет", True),
        ("Аннотация к задаче", False),
        ("Начни с README", False),
    ],
)
def test_fr_ag_09_name_declension_is_detected(text, found):
    assert mentions(text, ["Анна", "Олег"]) is found


async def test_fr_ag_09_own_name_does_not_retry():
    _, llm, registry, runtime, context = setup()
    card = (await runtime.data.team_card(context.user_id))["document"]
    own = next(person["name"] for person in card["people"] if person["user_id"] == context.user_id)
    llm.script = [{"content": f"Привет, {own}! Начни с README репозитория."}]
    decision, response = await Agent(llm, registry).answer("привет", context, runtime)
    assert decision.case == "chat" and own in response.result and len(llm.chats) == 1


async def test_fr_ag_09_card_error_skips_name_check():
    _, llm, registry, _, context = setup({"content": COPIED})

    class Data:
        async def team_card(self, user_id):
            raise DataError("unavailable")

    class Runtime:
        data = Data()

    decision, response = await Agent(llm, registry).answer("кто мой наставник?", context, Runtime())
    assert decision.case == "chat" and response.result == COPIED


async def test_fr_ag_05_client_sends_tool_choice_with_require_parameters():
    settings = live_settings("agent")
    llm, bodies = recording_llm(settings, "ok")
    await llm.chat(
        [{"role": "user", "content": "кто наставник?"}], tools_for("intern"), tool_choice="required"
    )
    await llm.chat([{"role": "user", "content": "кто наставник?"}], tools_for("intern"))
    assert bodies[0]["tool_choice"] == "required"
    assert bodies[0]["provider"] == {"data_collection": "deny", "require_parameters": True}
    assert "tool_choice" not in bodies[1] and bodies[1]["provider"] == {"data_collection": "deny"}


async def test_fr_ag_05_mentor_question_twice_gets_fresh_links():
    ask, *_ = setup(
        {"tool_calls": [call("team_q3")]},
        {"content": "Наставник — Анна [S1], тимлид и первый контакт — Олег [S2]."},
        {"content": COPIED},
        {"tool_calls": [call("team_q3", id="c2")]},
        {"content": "Наставник — Анна [S1], первый контакт — Олег [S2]."},
    )
    await ask("привет")
    state = await ask("кто мой наставник?")
    assert "agent" in state["trace"] and ANNA in state["response"].links
    assert "Олег" in state["response"].result and COPIED not in state["response"].result


# FR-AG-05, FR-AG-09: an access status without a tool this turn → one retry with a required access tool.

STALE_ACCESS = "Доступа к Miro у тебя пока нет, остальное есть."


async def test_fr_ag_05_access_status_without_tool_retries_with_access_tools():
    _, llm, registry, runtime, context = setup(
        {"content": STALE_ACCESS},
        {"tool_calls": [call("access_status")]},
        {"content": "Доступа к Miro пока нет [S1]."},
    )
    decision, response = await Agent(llm, registry).answer("какие у меня доступы?", context, runtime)
    assert decision.case == "access_status" and "[1]" in response.result and response.links
    assert STALE_ACCESS not in response.result
    assert [choice for choice, _ in llm.choices] == [None, "required", None]
    assert set(llm.choices[1][1]) == {"access_status", "access_task_analysis"}


async def test_fr_ag_05_name_and_access_offer_both_tool_sets():
    _, llm, registry, runtime, context = setup(
        {"content": "Анна выдаст доступ, а к Jira доступ уже есть."}, {"content": ""}
    )
    await Agent(llm, registry).answer("как с доступами?", context, runtime)
    offered = set(llm.choices[1][1])
    assert {"team_q3", "access_status"} <= offered and "brief_now" not in offered


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("У тебя есть доступ к Jira.", True),
        ("Доступа к Miro пока нет", True),
        ("✅ GitHub — доступ есть", True),
        ("Заявка на доступ к Google Calendar на рассмотрении.", True),
        ("Могу проверить доступы к GitHub, Jira, Miro, календарю.", False),
        ("Попроси доступ к Jira у наставника.", False),
        ("Есть вопросы по Jira?", False),
    ],
)
def test_fr_ag_09_access_status_is_detected(text, found):
    assert access_claim(text) is found


async def test_fr_ag_09_access_offer_without_status_passes():
    offer = "Могу проверить доступы к GitHub, Jira, Miro и календарю — просто спроси."
    _, llm, registry, runtime, context = setup({"content": offer})
    decision, response = await Agent(llm, registry).answer("привет", context, runtime)
    assert decision.case == "chat" and response.result == offer and len(llm.chats) == 1


async def test_fr_ag_10_turn_is_logged_without_user_text(caplog):
    ask, *_ = setup({"tool_calls": [call("team_q3")]}, {"content": "Наставник — Анна [S1]."})
    with caplog.at_level(logging.INFO, logger="buddy"):
        await ask("кто мой наставник? секрет-123")
    lines = [r.getMessage() for r in caplog.records if r.name == "buddy.graph.build"]
    assert lines == ["turn: case=team_q3 trace=agent llm_calls=2 links=1"]
    assert "секрет-123" not in caplog.text


def test_fr_ag_12_system_prompt_requires_status_icons():
    from buddy.access.service import ICONS
    from buddy.agent.loop import SYSTEM

    assert all(icon in SYSTEM for icon in ("✅", "⬜", "⏳", "⏰", "⛔", "❌"))
    assert set(ICONS.values()) == {"✅", "⬜", "⏳", "⏰", "⛔", "❌"}  # template and prompt agree
    assert "Не убирай значки" in SYSTEM and "«Есть», «Нет», «Закрыто»" in SYSTEM


def test_fr_ag_12_unverified_status_line_starts_with_cross():
    from datetime import UTC, datetime

    from buddy.access.checker import AccessItem
    from buddy.access.service import AccessService

    item = AccessItem("o1", "miro", "доска", "https://example.invalid/miro", "", "u1", "unknown")
    response = AccessService()._render([item], {}, datetime.now(UTC), "Доступы:")
    assert "❌ Miro — доска: не удалось проверить, попробуйте позже" in response.result


def test_fr_ag_13_system_prompt_has_style_block_with_icon_exception():
    from buddy.agent.loop import SYSTEM

    assert "Стиль ответа." in SYSTEM and "«не просто X, а Y»" in SYSTEM and "Таким образом" in SYSTEM
    assert "Эмодзи не используй. Исключение: значки статуса" in SYSTEM


def test_fr_ag_13_brief_and_playbook_prompts_have_short_style_rules():
    from buddy.brief.service import PROMPT as BRIEF
    from buddy.rag.qa import DETAILS_PROMPT, PROMPT

    for prompt in (BRIEF, PROMPT, DETAILS_PROMPT):
        assert "концовки-резюме" in prompt and "«не просто X, а Y»" in prompt and "вступления" in prompt


def test_fr_ag_13_eval_flags_ai_text_tells():
    run_agent = eval_module()
    assert run_agent.TELLS.search("Отличный вопрос! Доступ есть")
    assert run_agent.TELLS.search("Это не просто доска, а план спринта")
    assert not run_agent.TELLS.search("✅ GitHub — orders-onboarding: доступ есть [S1]")
    row = next(row for row in run_agent.load() if row["id"] == "agent-003")
    assert row["expect"] == ["✅", "⬜", "⛔"]


def test_fr_ag_13_system_prompt_keeps_one_fact_per_line():
    from buddy.agent.loop import SYSTEM

    assert "не сливай в абзац, каждый — с новой" in SYSTEM and "Главное — первой строкой" in SYSTEM
