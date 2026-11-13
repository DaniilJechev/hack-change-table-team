"""FR-AG-07: compare LLMs as the AI-buddy agent on demo fixtures.

Tools run on the synthetic demo data; only the LLM is real. Requires LLM_API_KEY in .env and
spends real money — run by hand, not in CI:

    uv run --project agent --frozen python evals/run_agent.py \
        --models deepseek/deepseek-v4.1-flash-1017 anthropic/claude-haiku-5.5
"""

import argparse
import asyncio
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from buddy.agent import Agent
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


def load(path: Path = CASES) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Recorder(httpx.AsyncBaseTransport):
    """Counts agent steps, tool calls and OpenRouter cost of every LLM response."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner
        self.reset()

    def reset(self) -> None:
        self.steps, self.cost, self.calls = 0, 0.0, []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        content = await response.aread()
        try:
            data = json.loads(content)
            self.cost += (data.get("usage") or {}).get("cost") or 0.0
            if "tools" in json.loads(request.content):
                self.steps += 1
                message = data["choices"][0]["message"]
                self.calls += [call["function"]["name"] for call in message.get("tool_calls") or []]
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
        self.items.append(str(record.args[0]) if record.args else record.getMessage())


async def run_case(settings: Settings, recorder: Recorder, reasons: Reasons, row: dict) -> dict:
    controller = DemoController(True)
    controller.command(1, "mentor" if row.get("role") == "mentor" else "")
    registry = Registry()
    llm = CompatibleLLM(settings, recorder)
    graph = build_graph(Router(llm), registry, Memory(), llm, Agent(llm, registry))
    base = {"context": controller.context(1), "runtime": controller.runtime(1)}
    for text in row.get("before", []):
        await graph.ainvoke({**base, "text": text})
    recorder.reset()
    reasons.items.clear()
    start = time.perf_counter()
    state = await graph.ainvoke({**base, "text": row["text"]})
    seconds = round(time.perf_counter() - start, 2)
    called = list(recorder.calls)
    if row.get("fast"):
        tool_ok = recorder.steps == 0
    else:
        tool_ok = set(row["tools"]) <= set(called) and not set(row.get("forbid", [])) & set(called)
    reason = reasons.items[0] if reasons.items else ""
    answer = state["response"].result
    return {
        "id": row["id"],
        "kind": row["kind"],
        "tools": called,
        "tool_ok": tool_ok,
        "answered": recorder.steps > 0 and not reason,
        # FR-AG-12: e.g. status icons that must survive the model's rewrite.
        "expect_ok": all(part in answer for part in row.get("expect", [])),
        "style_ok": not TELLS.search(answer),
        "fallback": reason,
        "unsupported_blocked": reason in UNSUPPORTED,
        "steps": recorder.steps,
        "seconds": seconds,
        "cost": round(recorder.cost, 6),
        "answer": answer[:600],
        "links": len(state["response"].links),
    }


async def run_model(settings: Settings, rows: list[dict], transport: httpx.AsyncBaseTransport | None = None):
    recorder = Recorder(transport or httpx.AsyncHTTPTransport())
    reasons = Reasons()
    logger = logging.getLogger("buddy.agent.loop")
    logger.addHandler(reasons)
    logger.setLevel(logging.INFO)
    try:
        # Sequential: fallback reasons and costs are attributed to one case at a time.
        return [await run_case(settings, recorder, reasons, row) for row in rows]
    finally:
        logger.removeHandler(reasons)


def summary(results: list[dict]) -> dict:
    agent = [r for r in results if r["steps"]]
    seconds = sorted(r["seconds"] for r in agent) or [0]
    return {
        "cases": len(results),
        "tool_ok": sum(r["tool_ok"] for r in results),
        "answered": sum(r["answered"] for r in results),
        "expect_ok": sum(r["expect_ok"] for r in results),
        "style_ok": sum(r["style_ok"] for r in results),
        "unsupported_blocked": sum(r["unsupported_blocked"] for r in results),
        "fallbacks": dict(Counter(r["fallback"] for r in results if r["fallback"])),
        "avg_seconds": round(sum(seconds) / len(seconds), 2),
        "p95_seconds": seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))],
        "cost": round(sum(r["cost"] for r in results), 4),
    }


async def compare(models: list[str], rows: list[dict], settings: Settings, transport=None) -> dict:
    report = {"cases": rows, "models": {}}
    for model in models:
        results = await run_model(settings.model_copy(update={"llm_model": model}), rows, transport)
        report["models"][model] = {"summary": summary(results), "results": results}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/agent/report.json")
    args = parser.parse_args(argv)
    settings = Settings(_env_file=ROOT / ".env", demo_mode=True, memory_enabled=True)
    if not settings.llm_api_key.get_secret_value():
        parser.error("LLM_API_KEY is empty in .env")
    report = asyncio.run(compare(args.models, load(), settings))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    for model, data in report["models"].items():
        s = data["summary"]
        print(
            f"{model}: tools {s['tool_ok']}/{s['cases']}, answered {s['answered']}, "
            f"blocked {s['unsupported_blocked']}, p95 {s['p95_seconds']} s, ${s['cost']:.4f}"
            f", {s['fallbacks']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
