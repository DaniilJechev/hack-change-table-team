"""Compare real LLMs on the Router task over all evaluation sets.

Uses the exact production Router prompt (buddy.router.service) and parser, but calls
OpenRouter directly so that cost, latency and raw answers are visible and an LLM failure
is not hidden by the rules fallback. Requires LLM_API_KEY in .env; spends real money.

    uv run --project agent --frozen python evals/run_llm_compare.py \
        --models anthropic/claude-haiku-5.5 deepseek/deepseek-v4.1-flash-1017
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from buddy.config import Settings
from buddy.router.rules import decide
from buddy.router.service import PARAMS, SYSTEM, parse
from buddy.schemas import RouteDecision

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "router": ROOT / "evals/router/cases.jsonl",
    "holdout": ROOT / "evals/router/holdout.jsonl",
    "h2": ROOT / "evals/h2/cases.jsonl",
    "h3": ROOT / "evals/h3/cases.jsonl",
}
PRODUCTION_TIMEOUT = 20.0


def prompt_for(text: str) -> str:
    # Mirrors Router.route without context, as evals/run_router.py does.
    return (
        SYSTEM
        + "\nСхема: "
        + json.dumps(RouteDecision.model_json_schema(), ensure_ascii=False)
        + "\nДопустимые параметры: "
        + json.dumps({k: sorted(v) for k, v in PARAMS.items()})
        + "\nДоверенный контекст (JSON): "
        + json.dumps({}, ensure_ascii=False)
        + "\nТекст пользователя (JSON): "
        + json.dumps(text, ensure_ascii=False)
    )


def load_rows() -> list[dict]:
    rows = []
    for name, path in DATASETS.items():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["dataset"] = name
                rows.append(row)
    return rows


async def call(client: httpx.AsyncClient, key: str, base: str, model: str, text: str) -> dict:
    last_error = ""
    for _ in range(3):  # retry transport errors only; model answers are never retried
        start = time.perf_counter()
        try:
            response = await client.post(
                base + "/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": model, "messages": [{"role": "user", "content": prompt_for(text)}]},
            )
            elapsed = time.perf_counter() - start
            data = response.json()
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {str(data.get('error', data))[:200]}"
                continue
            usage = data.get("usage") or {}
            return {
                "raw": data["choices"][0]["message"].get("content") or "",
                "seconds": round(elapsed, 2),
                "cost": usage.get("cost") or 0.0,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "error": "",
            }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_error = type(exc).__name__ + ": " + str(exc)[:200]
    return {"raw": "", "seconds": 0, "cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "error": last_error}


def judge(row: dict, result: dict) -> dict:
    accept = row.get("accept", [row["case"]])
    expected_params = row.get("params", {})
    llm_case, llm_params, fmt_ok = None, {}, False
    if not result["error"]:
        try:
            decision = parse(result["raw"])
            llm_case, llm_params, fmt_ok = decision.case, decision.params, True
        except Exception:
            pass
    timed_out = result["seconds"] > PRODUCTION_TIMEOUT
    llm_ok = fmt_ok and llm_case in accept and all(llm_params.get(k) == v for k, v in expected_params.items())
    # What the bot would really answer: LLM decision unless it failed/timed out, then rules.
    if fmt_ok and not timed_out:
        prod_case, prod_params = llm_case, llm_params
    else:
        fallback = decide(row["text"])
        prod_case, prod_params = fallback.case, fallback.params
    prod_ok = prod_case in accept and all(prod_params.get(k) == v for k, v in expected_params.items())
    return {
        **result,
        "case": llm_case,
        "params": llm_params,
        "format_ok": fmt_ok,
        "timed_out": timed_out,
        "llm_ok": llm_ok,
        "case_ok": fmt_ok and llm_case in accept,
        "prod_case": prod_case,
        "prod_ok": prod_ok,
    }


async def run_model(model: str, rows: list[dict], key: str, base: str, concurrency: int) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=90) as client:

        async def one(row: dict) -> dict:
            if row["text"].lstrip().startswith("/"):
                # Production never sends slash commands to the LLM.
                fallback = decide(row["text"])
                ok = fallback.case in row.get("accept", [row["case"]])
                return {"raw": "(команда, LLM не вызывается)", "seconds": 0, "cost": 0.0, "prompt_tokens": 0,
                        "completion_tokens": 0, "error": "", "case": fallback.case, "params": fallback.params,
                        "format_ok": True, "timed_out": False, "llm_ok": ok, "case_ok": ok,
                        "prod_case": fallback.case, "prod_ok": ok, "skipped": True}
            async with semaphore:
                return judge(row, await call(client, key, base, model, row["text"]))

        return await asyncio.gather(*(one(row) for row in rows))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/router/llm-compare.json")
    args = parser.parse_args(argv)
    settings = Settings(_env_file=ROOT / ".env", demo_mode=True)
    key = settings.llm_api_key.get_secret_value()
    if not key:
        parser.error("LLM_API_KEY is empty in .env")
    base = (settings.llm_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    rows = load_rows()
    report = {"rows": [{k: row.get(k) for k in ("id", "dataset", "text", "case", "accept", "params")} for row in rows],
              "models": {}}
    for model in args.models:
        print("running", model, "on", len(rows), "phrases...", flush=True)
        report["models"][model] = asyncio.run(run_model(model, rows, key, base, args.concurrency))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for model, results in report["models"].items():
        n = len(results)
        print(f"{model}: llm {sum(r['llm_ok'] for r in results)}/{n}, prod {sum(r['prod_ok'] for r in results)}/{n}, "
              f"${sum(r['cost'] for r in results):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
