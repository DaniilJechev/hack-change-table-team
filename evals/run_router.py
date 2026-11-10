"""Reproducible Router evaluation. Real LLM calls require an explicit --mode llm."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from buddy.config import Settings
from buddy.llm.client import CompatibleLLM
from buddy.router.service import CASES, Router

ROOT = Path(__file__).resolve().parents[1]


async def evaluate(router: Router, rows: list[dict]) -> dict:
    matrix = {name: {} for name in CASES}
    failures = []
    correct = 0
    parameter_errors = 0
    for row in rows:
        result = await router.route(row["text"])
        expected = row["case"]
        matrix[expected][result.case] = matrix[expected].get(result.case, 0) + 1
        matching = all(result.params.get(key) == value for key, value in row.get("params", {}).items())
        correct += result.case == expected
        parameter_errors += not matching
        if result.case != expected or not matching:
            # IDs and classes only: no source data, prompts or model responses in reports.
            failures.append(
                {"id": row["id"], "expected": expected, "actual": result.case, "parameters_match": matching}
            )
    return {
        "total": len(rows),
        "accuracy": correct / len(rows) if rows else 0,
        "parameter_errors": parameter_errors,
        "case_counts": dict(Counter(row["case"] for row in rows)),
        "confusion_matrix": matrix,
        "failures": failures,
    }


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset needs unique IDs and at least one example")
    if any(row["case"] not in CASES or not isinstance(row["text"], str) for row in rows):
        raise ValueError("Invalid router dataset")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/router/cases.jsonl")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    llm = None
    if args.mode == "llm":
        settings = Settings(_env_file=ROOT / ".env", demo_mode=True)
        if (
            settings.llm_provider == "fake"
            or not settings.llm_model
            or not settings.llm_api_key.get_secret_value()
        ):
            parser.error("LLM mode requires configured LLM_PROVIDER, LLM_MODEL and LLM_API_KEY")
        llm = CompatibleLLM(settings)
    report = asyncio.run(evaluate(Router(llm), load(args.dataset)))
    report.update(mode=args.mode, synthetic=True, real_llm_requested=args.mode == "llm")
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
