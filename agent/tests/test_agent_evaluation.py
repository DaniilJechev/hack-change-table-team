"""EV-01..03: challenge the evaluator itself with deliberately broken answers."""

import json

import httpx
import pytest
from test_agent_orchestrator import eval_module, live_settings


@pytest.fixture
def ev():
    return eval_module()


def test_ev_01_no_criteria_is_not_evaluated(ev):
    result = ev.assess({"tools": []}, "Привет!", [], [], [])
    assert result["status"] == "not_evaluated" and result["checks"]["content"] is None
    result = ev.assess({"quality": {"all": [], "patterns": []}}, "Привет!", [], [], [])
    assert result["checks"]["content"] is None


@pytest.mark.parametrize(
    ("answer", "failure"),
    [
        ("Твой наставник — Анна. Доступа к Miro нет.", None),
        ("Твой наставник — Олег. Доступа к Miro нет.", "content"),
        ("Твой наставник — Анна.", "content"),
        ("Твой наставник — Анна. Доступ к Miro выдан.", "content"),
        ("Твой наставник — Анна. Доступа к Miro нет. Олег уволен.", "content"),
        ("Не знаю.", "content"),
        ("回答前不需要调用工具。Твой наставник — Анна. Доступа к Miro нет.", "no_internal_text"),
        ("No need to call tools. Твой наставник — Анна. Доступа к Miro нет.", "no_internal_text"),
        ("Перед ответом не нужно вызывать инструменты. Анна, доступа к Miro нет.", "no_internal_text"),
    ],
)
def test_ev_02_controlled_answer_mutations(ev, answer, failure):
    row = {
        "tools": [],
        "quality": {
            "all": ["Анна", "доступа к Miro нет"],
            "none": ["Олег уволен"],
            "sources": ["https://example.invalid/anna"],
        },
    }
    result = ev.assess(row, answer, ["https://example.invalid/anna"], [], [])
    if failure:
        assert failure in result["failures"]
    else:
        assert result["status"] == "pass"


def test_ev_01_wrong_source_with_correct_person_fails(ev):
    row = {"quality": {"all": ["Анна"], "sources": ["https://example.invalid/anna"]}}
    assert "content" in ev.assess(row, "Анна [1]", ["https://example.invalid/oleg"], [], [])["failures"]


def test_ev_02_correct_refusal_and_invented_leave_policy(ev):
    row = next(r for r in ev.load() if r["id"] == "agent-025")
    row.pop("tools")
    good = "В playbook этого нет. Playbook публичный, может быть устаревшим."
    assert ev.assess(row, good, [], [], [])["status"] == "pass"
    assert ev.assess(row, "В playbook этого нет. Положено 28 дней.", [], [], [])["status"] == "fail"


def test_ev_02_terms_code_and_authorized_quote_are_allowed(ev):
    row = {"tools": [], "quality": {"all": ["GitHub"], "allowed_quotes": ["你好"]}}
    result = ev.assess(row, 'В GitHub проверь PR. Цитата: 你好. ```print("Hello world")```', [], [], [])
    assert result["status"] == "pass"
    assert ev.assess(row, "你好 GitHub", [], [], [])["checks"]["language"] is True
    assert ev.assess(row, "工具 GitHub", [], [], [])["checks"]["language"] is False


def test_ev_01_tools_empty_unrestricted_extra_and_arguments(ev):
    calls = [{"name": "team_q3", "arguments": {}}]
    assert ev.assess({"tools": []}, "Привет", [], calls, calls)["checks"]["tools"] is False
    assert ev.assess({}, "Привет", [], calls, calls)["checks"]["tools"] is None
    row = {"tools": ["team_q3"], "arguments": {"team_q3": {"person": "Анна"}}}
    assert ev.assess(row, "Привет", [], calls, calls)["checks"]["arguments"] is False
    assert ev.assess(row, "Привет", [], calls, [])["checks"]["execution"] is False
    assert ev.assess({"tools": []}, "Привет", [], [], calls)["checks"]["tools"] is True


def test_ev_01_fallback_is_explicit(ev):
    row = {"quality": {"all": ["Анна"]}}
    assert ev.assess(row, "Анна", [], [], [], "timeout")["status"] == "fail"
    row["quality"]["allow_fallback"] = True
    assert ev.assess(row, "Анна", [], [], [], "timeout")["status"] == "pass"


async def test_ev_03_budget_counts_history_and_all_models(ev):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Привет!"}}]})

    rows = [
        {
            "id": "history",
            "kind": "open",
            "text": "Привет",
            "before": ["Привет"],
            "tools": [],
            "quality": {"all": ["Привет"]},
        }
    ]
    report = await ev.compare(
        ["a", "b"], rows, live_settings("agent"), httpx.MockTransport(handler), max_requests=2, repeats=2
    )
    assert len(sent) == report["budget"]["used"] == 2
    assert report["budget"]["exhausted"] and not ev.accepted(report)
    assert "test-key" not in json.dumps(report)


def test_ev_03_acceptance_rejects_missing_criteria_and_critical_failures(ev):
    result = ev.assess({"quality": {"all": ["Привет"]}}, "Привет", [], [], [])
    report = {
        "models": {"a": {"results": [result]}},
        "expected_results_per_model": 1,
        "budget": {"exhausted": False},
    }
    assert ev.accepted(report)
    result["checks"]["content"] = None
    assert not ev.accepted(report)
    result["checks"]["content"] = True
    result["checks"]["no_internal_text"] = False
    assert not ev.accepted(report, 0)


def test_ev_03_every_current_case_has_content_and_task_arguments(ev):
    for row in ev.load():
        assert row["quality"] and any(row["quality"].get(k) for k in ("all", "any", "patterns"))
        if "access_task_analysis" in row.get("tools", []):
            assert row["arguments"]["access_task_analysis"]["task"]


def test_ev_01_dataset_rejects_misspelled_criteria(ev, tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"id": "bad", "quality": {"al": ["Анна"]}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown quality"):
        ev.load(path)


def test_ev_03_cli_saves_report_and_fails_on_internal_text(ev, monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "回答前不需要调用工具。Привет!"}}]}
        )

    monkeypatch.setattr(ev.httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(ev, "Settings", lambda **kwargs: live_settings("agent"))
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {"id": "leak", "kind": "open", "text": "Привет", "tools": [], "quality": {"all": ["Привет"]}}
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    assert ev.main(["--dataset", str(path), "--output", str(output), "--max-requests", "1"]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    result = next(iter(report["models"].values()))["results"][0]
    assert "no_internal_text" in result["failures"] and report["budget"]["responses"] == 1


async def test_ev_03_transport_failure_is_not_model_quality(ev):
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    rows = [{"id": "offline", "kind": "open", "text": "Привет", "tools": [], "quality": {"all": ["Привет"]}}]
    report = await ev.compare(["a"], rows, live_settings("agent"), httpx.MockTransport(handler))
    assert report["budget"]["responses"] == 0
    assert report["models"]["a"]["summary"]["infrastructure_errors"] == {"ConnectError": 1}
    assert not ev.accepted(report, 0)


def test_ev_03_retry_log_is_not_fallback(ev):
    import logging

    handler = ev.Reasons()
    handler.emit(logging.LogRecord("test", logging.INFO, "", 1, "agent retry: %s", ("team",), None))
    assert handler.items == []
    handler.emit(logging.LogRecord("test", logging.INFO, "", 1, "agent fallback: %s", ("label",), None))
    assert handler.items == ["label"]


def test_ev_02_russian_technology_list_and_english_prose(ev):
    row = {"quality": {"all": ["Go"]}}
    answer = (
        "Команда пишет на Go, с PostgreSQL и Kafka. Ещё использует Python, "
        "GitHub, Jira, Miro, Google Calendar и Telegram."
    )
    assert ev.assess(row, answer, [], [], [])["checks"]["language"]
    english = "The team uses Go and you should always ask your mentor before changing the system."
    assert not ev.assess(row, english, [], [], [])["checks"]["language"]


@pytest.mark.parametrize(
    ("case_id", "good", "bad"),
    [
        (
            "agent-019",
            "Привет! Показываю, что нового по PR, задачам и встречам, доступы, команду и playbook.",
            "Привет! Покажу доступы, команду и playbook.",
        ),
        ("agent-024", "Не получилось получить задачу PAY-999 из Jira.", "Для PAY-999 нужны все доступы."),
        (
            "agent-031",
            "В ORD-121 инструменты явно не указаны.",
            "Не удалось получить задачу ORD-121 из Jira.",
        ),
        ("agent-032", "У Даниила и Эмиля проверок ещё не было.", "У Даниила проверок ещё не было."),
    ],
)
def test_ev_02_calibrated_wording_keeps_required_meaning(ev, case_id, good, bad):
    row = next(r for r in ev.load() if r["id"] == case_id)
    links = row["quality"].get("sources", [])
    assert ev.assess(row, good, links, [], [])["checks"]["content"]
    assert not ev.assess(row, bad, links, [], [])["checks"]["content"]


async def test_ev_03_next_case_history_cannot_mutate_previous_calls(ev):
    def handler(request):
        messages = json.loads(request.content)["messages"]
        last = messages[-1]
        if last["role"] == "user" and last["content"] == "Кто мой наставник?":
            message = {
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "team_q3", "arguments": "{}"}}
                ]
            }
        else:
            message = {"content": "Наставник — Анна [S1]." if last["role"] == "tool" else "Привет!"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    rows = [
        {"id": "first", "kind": "open", "text": "Привет", "tools": [], "quality": {"all": ["Привет"]}},
        {
            "id": "second",
            "kind": "open",
            "before": ["Кто мой наставник?"],
            "text": "Привет",
            "tools": [],
            "quality": {"all": ["Привет"]},
        },
    ]
    results = await ev.run_model(live_settings("agent"), rows, httpx.MockTransport(handler))
    assert results[0]["calls"] == []
    assert results[0]["tools"] == [] and results[0]["tool_ok"]
    assert results[1]["history_requests"] == 2


def test_ev_01_unrendered_source_label_fails(ev):
    row = {"quality": {"all": ["ценности"]}}
    assert ev.assess(row, "Вот ценности [S1–S5].", [], [], [])["checks"]["citation_labels"] is False
    assert ev.assess(row, "Вот ценности [1][2].", [], [], [])["checks"]["citation_labels"] is True


@pytest.mark.parametrize("case_id", ["agent-013", "agent-014", "agent-025"])
def test_ev_01_playbook_requires_freshness_warning(ev, case_id):
    row = next(r for r in ev.load() if r["id"] == case_id)
    missing = " ".join(row["quality"].get("all", []))
    verdict = ev.assess(row, missing, row["quality"].get("sources", []), [], [])
    assert not verdict["content_checks"]["patterns"]


def test_ev_02_no_mentions_paraphrase_and_false_granted_claim(ev):
    row = next(r for r in ev.load() if r["id"] == "agent-031")
    answer = "По ORD-121 явных упоминаний инструментов в задаче нет."
    assert ev.assess(row, answer, row["quality"]["sources"], [], [])["checks"]["content"]
    row = next(r for r in ev.load() if r["id"] == "agent-018")
    links = row["quality"]["sources"]
    good = "Miro нет, запросить у Олега. Production запрещён."
    assert ev.assess(row, good, links, [], [])["checks"]["content"]
    bad = "Miro нет, запросить у Олега, остальное уже есть."
    assert not ev.assess(row, bad, links, [], [])["checks"]["content"]
