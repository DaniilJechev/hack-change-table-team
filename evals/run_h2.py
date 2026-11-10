"""Reproducible H2 evaluation on synthetic demo data: Router rules → H2 executor → answer checks.

Checks per question: the Router chose the expected case, the answer contains the expected fact,
and every factual answer carries at least one source link (NFR-LINK-1). No network, no LLM.
"""

import argparse
import asyncio
import json
from pathlib import Path

from buddy.demo.controller import DemoController
from buddy.executors.core import Registry
from buddy.router.service import Router

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset needs unique IDs and at least one example")
    return rows


async def evaluate(rows: list[dict]) -> dict:
    router = Router(None)
    failures, routed, answered, linked = [], 0, 0, 0
    for index, row in enumerate(rows):
        controller = DemoController(True)
        controller.command(index)
        context, runtime = controller.context(index), controller.runtime(index)
        decision = await router.route(row["text"], context=context)
        response = await Registry().execute(decision.case, decision.params, context, runtime, row["text"])
        case_ok = decision.case == row["case"]
        fact_ok = row["expect"] in response.result
        link_ok = bool(response.links)
        routed += case_ok
        answered += fact_ok
        linked += link_ok
        if not (case_ok and fact_ok and link_ok):
            failures.append(
                {
                    "id": row["id"],
                    "expected_case": row["case"],
                    "actual_case": decision.case,
                    "fact": fact_ok,
                    "link": link_ok,
                }
            )
    total = len(rows)
    return {
        "total": total,
        "routing_accuracy": routed / total,
        "answer_accuracy": answered / total,
        "answers_with_links": linked / total,
        "failures": failures,
        "mode": "rules",
        "synthetic": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/h2/cases.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/h2/rules-report.json")
    args = parser.parse_args(argv)
    report = asyncio.run(evaluate(load(args.dataset)))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
