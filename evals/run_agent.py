"""FR-AG-07: compare LLMs as the AI-buddy agent on demo fixtures.

Tools run on the synthetic demo data; only the LLM is real. Requires LLM_API_KEY in .env and
spends real money — run by hand, not in CI:

    uv run --project agent --frozen python evals/run_agent.py \
        --models deepseek/deepseek-v4.1-flash-1017 anthropic/claude-haiku-5.5
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from buddy.agent import Agent
from buddy.agent.loop import SYSTEM
from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.executors.core import Registry
from buddy.graph import build_graph
from buddy.llm.client import CompatibleLLM
from buddy.memory import Memory
from buddy.router.service import Router

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/agent/cases.jsonl"
# Fallback reasons meaning the model wrote something unsupported and code blocked it (FR-AG-09).
UNSUPPORTED = {"answer", "label", "task", "pr", "unlabelled"}
# FR-AG-13: AI-text tells from reports/AI_TEXT_TELLS_PROMPT.md that code can spot in an answer.
TELLS = re.compile(
    r"^(?:Отличный вопрос|Конечно|Разумеется|Давай разберёмся|Рад помочь)|не просто .{1,60}?, а "
    r"|Таким образом|Надеюсь, (?:это )?помо|стоит отметить|важно понимать|играет ключевую роль",
    re.IGNORECASE,
)
LEAK = re.compile(
    r"回答前不需要调用工具|(?:не нужно|необходимо|нужно|следует) (?:вызывать|вызвать) инструмент"
    r"|(?:перед ответом|сначала я) .{0,60}(?:инструмент|рассужд)"
    r"|(?:system prompt|chain.of.thought|no need to (?:call|use) tools|системный промпт)",
    re.IGNORECASE,
)
CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
TECH_TERMS = re.compile(
    r"\b(?:Go|PostgreSQL|Kafka|Python|GitHub|Jira|Miro|Google Calendar|Calendar|Telegram|"
    r"AvitoTech|Playbook|PR|API|SQL|Redis|Docker)\b", re.IGNORECASE
)
INFRASTRUCTURE_ERRORS = {"ConnectError", "ConnectTimeout", "ProxyError", "ReadTimeout", "HTTPStatusError"}


def assess(
    row: dict,
    answer: str,
    links: list[str],
    calls: list[dict],
    executions: list[dict],
    fallback: str = "",
) -> dict:
    """EV-01/02: explicit mechanical checks, never a semantic correctness claim."""
    quality = row.get("quality", {})
    text = answer.casefold().replace("ё", "е")

    def norm(value):
        return value.casefold().replace("ё", "е")

    checks = {}
    content = {}
    for key in ("all", "any", "none"):
        if quality.get(key):
            values = quality[key]
            content[key] = (
                all(norm(v) in text for v in values)
                if key == "all"
                else any(norm(v) in text for v in values)
                if key == "any"
                else all(norm(v) not in text for v in values)
            )
    if quality.get("patterns"):
        content["patterns"] = all(re.search(p, answer, re.IGNORECASE) for p in quality["patterns"])
        content["patterns"] = bool(content["patterns"])
    if quality.get("sources"):
        content["sources"] = set(quality["sources"]) <= set(links)
    if "allowed_sources" in quality:
        content["allowed_sources"] = set(links) <= set(quality["allowed_sources"])
    checks["content"] = all(content.values()) if content else None
    checks["nonempty"] = bool(answer.strip())
    # Remove only case-authorized quotations and fenced code for the script heuristic.
    visible = re.sub(r"```.*?```", "", answer, flags=re.DOTALL)
    for quote in quality.get("allowed_quotes", []):
        visible = visible.replace(quote, "")
    checks["no_internal_text"] = not LEAK.search(visible)
    checks["citation_labels"] = not re.search(r"\[S\d[^\]]*\]", visible)
    latin = re.findall(r"\b[A-Za-z]{2,}\b", TECH_TERMS.sub("", visible))
    russian = re.findall(r"[А-Яа-яЁё]{2,}", visible)
    checks["language"] = not CJK.search(visible) and not (len(latin) > 8 and len(latin) > len(russian))
    checks["style"] = not TELLS.search(answer)
    checks["fallback"] = not fallback or quality.get("allow_fallback", False)
    names = [c["name"] for c in calls]
    if "tools" in row:
        expected = set(row["tools"])
        checks["tools"] = expected <= set(names) and (
            row.get("allow_extra_tools", False) or set(names) <= expected
        )
    else:
        checks["tools"] = None
    checks["forbidden_tools"] = not set(row.get("forbid", [])) & set(names)
    checks["arguments"] = all(
        any(
            c["name"] == tool
            and isinstance(c["arguments"], dict)
            and all(c["arguments"].get(k) == v for k, v in expected.items())
            for c in calls
        )
        for tool, expected in row.get("arguments", {}).items()
    )
    checks["execution"] = all(
        any(e["name"] == c["name"] and e["arguments"] == c["arguments"] for e in executions) for c in calls
    )
    if row.get("fast"):
        checks["tools"] = not calls
    failures = [key for key, passed in checks.items() if passed is False]
    return {
        "checks": checks,
        "content_checks": content,
        "status": "fail" if failures else "not_evaluated" if checks["content"] is None else "pass",
        "failures": failures,
    }


class RequestBudgetExceeded(RuntimeError):
    pass


class RequestBudget:
    """One counter shared across models, repeats and history; increment BEFORE network IO."""

    def __init__(self, limit=200):
        self.limit, self.used, self.exhausted = limit, 0, False
        self.responses = 0
        self.cost = 0.0

    def take(self):
        if self.used >= self.limit:
            self.exhausted = True
            raise RequestBudgetExceeded("LLM HTTP request budget exhausted")
        self.used += 1


class ExecutionRecorder(Registry):
    def __init__(self):
        super().__init__()
        self.executions = []

    async def execute(self, case, params, context, runtime=None, text=""):
        response = await super().execute(case, params, context, runtime, text)
        self.executions.append(
            {
                "name": case,
                "arguments": dict(params),
                "links": list(response.links),
                "answer": response.result,
            }
        )
        return response


def load(path: Path = CASES) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset must be nonempty with unique IDs")
    allowed = {
        "all",
        "any",
        "none",
        "patterns",
        "sources",
        "allowed_sources",
        "allow_fallback",
        "allowed_quotes",
    }
    for row in rows:
        quality = row.get("quality", {})
        if not isinstance(quality, dict) or set(quality) - allowed:
            raise ValueError(f"Unknown quality criteria: {row['id']}")
        for key, value in quality.items():
            if key == "allow_fallback":
                valid = isinstance(value, bool)
            else:
                valid = isinstance(value, list) and all(isinstance(v, str) and v for v in value)
            if not valid:
                raise ValueError(f"Invalid quality criterion {key}: {row['id']}")
        for pattern in quality.get("patterns", []):
            re.compile(pattern)
    return rows


class Recorder(httpx.AsyncBaseTransport):
    """Counts agent steps, tool calls and OpenRouter cost of every LLM response."""

    def __init__(self, inner: httpx.AsyncBaseTransport, budget=None):
        self.inner = inner
        self.budget = budget or RequestBudget()
        self.reset()

    def reset(self) -> None:
        self.steps, self.cost, self.calls, self.call_details = 0, 0.0, [], []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.budget.take()
        response = await self.inner.handle_async_request(request)
        self.budget.responses += 1
        content = await response.aread()
        try:
            data = json.loads(content)
            cost = (data.get("usage") or {}).get("cost") or 0.0
            self.cost += cost
            self.budget.cost += cost
            if "tools" in json.loads(request.content):
                self.steps += 1
                message = data["choices"][0]["message"]
                self.calls += [call["function"]["name"] for call in message.get("tool_calls") or []]
                for call in message.get("tool_calls") or []:
                    function = call["function"]
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                    except (ValueError, TypeError):
                        arguments = None
                    self.call_details.append({"name": function["name"], "arguments": arguments})
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        # The body is already decoded: drop encoding headers, or httpx decodes it twice (DecodingError).
        headers = [
            (name, value)
            for name, value in response.headers.items()
            if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        ]
        return httpx.Response(response.status_code, headers=headers, content=content, request=request)


class Reasons(logging.Handler):
    def __init__(self):
        super().__init__()
        self.items: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "agent fallback: %s":
            self.items.append(str(record.args[0]))


async def run_case(settings: Settings, recorder: Recorder, reasons: Reasons, row: dict) -> dict:
    controller = DemoController(True)
    controller.command(1, "mentor" if row.get("role") == "mentor" else "")
    registry = ExecutionRecorder()
    llm = CompatibleLLM(settings, recorder)
    graph = build_graph(Router(llm), registry, Memory(), llm, Agent(llm, registry))
    base = {"context": controller.context(1), "runtime": controller.runtime(1)}
    before_requests = recorder.budget.used
    for text in row.get("before", []):
        await graph.ainvoke({**base, "text": text})
    warmup_requests = recorder.budget.used - before_requests
    recorder.reset()
    registry.executions.clear()
    reasons.items.clear()
    start = time.perf_counter()
    state = await graph.ainvoke({**base, "text": row["text"]})
    seconds = round(time.perf_counter() - start, 2)
    called = list(recorder.calls)
    reason = reasons.items[0] if reasons.items else ""
    answer = "\n".join([state["response"].result, *state["response"].folded])
    verdict = assess(
        row,
        answer,
        state["response"].links,
        recorder.call_details,
        registry.executions,
        reason,
    )
    if row.get("fast") and recorder.steps:
        verdict["checks"]["tools"] = False
        verdict["status"] = "fail"
        verdict["failures"].append("tools")
    if recorder.budget.exhausted:
        verdict["status"] = "fail"
        verdict["failures"].append("request_budget")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "tools": called,
        "tool_ok": verdict["checks"]["tools"],
        "answered": recorder.steps > 0 and not reason,
        # FR-AG-12: e.g. status icons that must survive the model's rewrite.
        "expect_ok": all(part in answer for part in row["expect"]) if "expect" in row else None,
        "style_ok": all(verdict["checks"][key] for key in ("style", "language", "no_internal_text")),
        "fallback": reason,
        "infrastructure_error": reason if reason in INFRASTRUCTURE_ERRORS else "",
        "unsupported_blocked": reason in UNSUPPORTED,
        "steps": recorder.steps,
        "seconds": seconds,
        "cost": round(recorder.cost, 6),
        "requests_including_history": recorder.budget.used - before_requests,
        "history_requests": warmup_requests,
        "answer": answer,
        "links": len(state["response"].links),
        "source_urls": state["response"].links,
        "calls": deepcopy(recorder.call_details),
        "executions": registry.executions,
        **verdict,
    }


async def run_model(
    settings: Settings,
    rows: list[dict],
    transport: httpx.AsyncBaseTransport | None = None,
    budget=None,
):
    recorder = Recorder(transport or httpx.AsyncHTTPTransport(), budget)
    reasons = Reasons()
    logger = logging.getLogger("buddy.agent.loop")
    previous_level = logger.level
    logger.addHandler(reasons)
    logger.setLevel(logging.INFO)
    try:
        # Sequential: fallback reasons and costs are attributed to one case at a time.
        results = []
        for row in rows:
            try:
                results.append(await run_case(settings, recorder, reasons, row))
                if recorder.budget.exhausted:
                    break
            except RequestBudgetExceeded:
                break
        return results
    finally:
        logger.removeHandler(reasons)
        logger.setLevel(previous_level)
        await recorder.inner.aclose()


def summary(results: list[dict]) -> dict:
    agent = [r for r in results if r["steps"]]
    seconds = sorted(r["seconds"] for r in agent) or [0]
    return {
        "cases": len(results),
        "tool_ok": sum(r["tool_ok"] is True for r in results),
        "answered": sum(r["answered"] for r in results),
        "expect_ok": sum(r["expect_ok"] is True for r in results),
        "expect_evaluated": sum(r["expect_ok"] is not None for r in results),
        "passed": sum(r.get("status") == "pass" for r in results),
        "not_evaluated": sum(r.get("status") == "not_evaluated" for r in results),
        "failures": dict(Counter(f for r in results for f in r.get("failures", []))),
        "infrastructure_errors": dict(
            Counter(r["infrastructure_error"] for r in results if r.get("infrastructure_error"))
        ),
        "style_ok": sum(r["style_ok"] for r in results),
        "unsupported_blocked": sum(r["unsupported_blocked"] for r in results),
        "fallbacks": dict(Counter(r["fallback"] for r in results if r["fallback"])),
        "avg_seconds": round(sum(seconds) / len(seconds), 2),
        "p95_seconds": seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))],
        "cost": round(sum(r["cost"] for r in results), 4),
    }


async def compare(
    models: list[str],
    rows: list[dict],
    settings: Settings,
    transport=None,
    max_requests=200,
    repeats=1,
) -> dict:
    budget = RequestBudget(max_requests)
    report = {
        "cases": rows,
        "models": {},
        "metadata": metadata(settings, rows),
        "expected_results_per_model": len(rows) * repeats,
    }
    for model in models:
        results = await run_model(
            settings.model_copy(update={"llm_model": model}),
            rows * repeats,
            transport,
            budget,
        )
        report["models"][model] = {"summary": summary(results), "results": results}
        for index, result in enumerate(results):
            result["repeat"] = index // len(rows) + 1
    report["budget"] = {
        "limit": budget.limit,
        "used": budget.used,
        "responses": budget.responses,
        "cost_including_history": round(budget.cost, 6),
        "exhausted": budget.exhausted,
    }
    return report


def metadata(settings, rows):
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.DEVNULL
        ).strip()

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    return {
        "started_at": datetime.now(UTC).isoformat(),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "working_diff_sha256": digest(git("diff", "HEAD")),
        "prompt_sha256": digest(SYSTEM),
        "dataset_sha256": digest(json.dumps(rows, ensure_ascii=False, sort_keys=True)),
        "evaluator_sha256": digest(Path(__file__).read_text(encoding="utf-8")),
        "settings": {
            "provider": settings.llm_provider,
            "timeout": settings.llm_timeout_seconds,
            "mode": settings.agent_mode,
            "max_tokens": 800,
            "reasoning": False,
        },
    }


def accepted(report, min_pass_rate=0.9):
    if report["budget"]["exhausted"] or not report["models"]:
        return False
    for data in report["models"].values():
        results = data["results"]
        if len(results) != report["expected_results_per_model"] or not results:
            return False
        if any(
            r["checks"][k] is not True
            for r in results
            for k in ("no_internal_text", "language", "forbidden_tools")
        ):
            return False
        if any(r["checks"]["content"] is None for r in results):
            return False
        if any(r.get("infrastructure_error") for r in results):
            return False
        if sum(r["status"] == "pass" for r in results) / len(results) < min_pass_rate:
            return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--dataset", type=Path, default=CASES)
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--min-pass-rate", type=float, default=0.9)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/agent/report.json")
    args = parser.parse_args(argv)
    if not 1 <= args.max_requests <= 200 or args.repeats < 1 or not 0 <= args.min_pass_rate <= 1:
        parser.error("require 1 <= max-requests <= 200, repeats >= 1, 0 <= min-pass-rate <= 1")
    settings = Settings(_env_file=ROOT / ".env", demo_mode=True, memory_enabled=True)
    if not settings.llm_api_key.get_secret_value():
        parser.error("LLM_API_KEY is empty in .env")
    models = args.models or [settings.llm_model]
    if not all(models):
        parser.error("Set LLM_MODEL or --models")
    report = asyncio.run(
        compare(
            models,
            load(args.dataset),
            settings,
            max_requests=args.max_requests,
            repeats=args.repeats,
        )
    )
    report["acceptance"] = {
        "min_pass_rate": args.min_pass_rate,
        "passed": accepted(report, args.min_pass_rate),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for model, data in report["models"].items():
        s = data["summary"]
        print(
            f"{model}: tools {s['tool_ok']}/{s['cases']}, answered {s['answered']}, "
            f"blocked {s['unsupported_blocked']}, p95 {s['p95_seconds']} s, ${s['cost']:.4f}"
            f", {s['fallbacks']}"
        )
    print(
        f"Requests: {report['budget']['used']}/{args.max_requests}; "
        f"accepted: {report['acceptance']['passed']}"
    )
    return 0 if report["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
