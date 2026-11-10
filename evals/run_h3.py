"""H3 evaluation set: validation and routing check (Stage 3).

Retrieval metrics (source in top-3 ≥ 90 %, refusal on out-of-corpus questions) are measured in
Stage 4, when `rag/ingest.py` and `playbook_qa` exist. Today the script checks that the dataset is
consistent with the pinned playbook snapshot and measures how the rules Router routes the questions.
No network, no LLM.
"""

import argparse
import asyncio
import json
from pathlib import Path

from buddy.router.service import Router

ROOT = Path(__file__).resolve().parents[1]
# avito-tech/playbook @ 0c1d2aa1501ca6889357908f54b12695f0534e48 — content files of the snapshot.
PLAYBOOK_FILES = {
    "QA-profile.md",
    "analytics-levels.md",
    "analytics-management.md",
    "avito-developer-practice.md",
    "avito-history.md",
    "avito-open-source.md",
    "avito-paas.md",
    "bi-developers.md",
    "books.md",
    "design-levels.md",
    "design-management.md",
    "developer-profile.md",
    "development-principles.md",
    "ds-managers-profiles.md",
    "ds-skills.md",
    "goal-setting.md",
    "mission-and-values.md",
    "neighborhood-agreement.md",
    "processes-and-standards.md",
    "product-levels.md",
    "readme.md",
    "recruitment-and-office.md",
    "spt.md",
    "structure.md",
    "students.md",
    "tech_design_review.md",
    "techlead-profile.md",
    "training-and-development.md",
}


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset needs unique IDs and at least one example")
    for row in rows:
        if row["in_corpus"] and row["source"] not in PLAYBOOK_FILES:
            raise ValueError(f"{row['id']}: unknown playbook file {row['source']}")
        if not row["in_corpus"] and row["source"] is not None:
            raise ValueError(f"{row['id']}: out-of-corpus question must not have a source")
    return rows


async def evaluate(rows: list[dict]) -> dict:
    router = Router(None)
    failures = []
    for row in rows:
        decision = await router.route(row["text"])
        if decision.case not in row.get("accept", [row["case"]]):
            failures.append({"id": row["id"], "expected": row["case"], "actual": decision.case})
    return {
        "total": len(rows),
        "in_corpus": sum(row["in_corpus"] for row in rows),
        "out_of_corpus": sum(not row["in_corpus"] for row in rows),
        "sources": len({row["source"] for row in rows if row["in_corpus"]}),
        "routing_accuracy": (len(rows) - len(failures)) / len(rows),
        "failures": failures,
        "retrieval": "not measured: playbook_qa is Stage 4",
        "mode": "rules",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/h3/cases.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/h3/rules-report.json")
    args = parser.parse_args(argv)
    report = asyncio.run(evaluate(load(args.dataset)))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
