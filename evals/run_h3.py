"""H3 evaluation: Router rules + playbook retrieval on the pinned snapshot.

Metrics: routing accuracy; the expected file in top-1/top-3 for in-corpus questions; refusal for
out-of-corpus questions; false refusals. `--mode bm25` (default) needs no model, no database and no
network. `--mode vector|rrf` needs the `rag` extra, RAG_DATABASE_URL and a finished `make ingest`.
No LLM is called: the answer wording is not evaluated here.
"""

import argparse
import asyncio
import json
from pathlib import Path

from buddy.config import Settings
from buddy.rag.index import PlaybookIndex
from buddy.rag.search import PlaybookSearch, create_search
from buddy.router.service import Router

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, index: PlaybookIndex | None = None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset needs unique IDs and at least one example")
    files = {chunk.file for chunk in (index or PlaybookIndex.load()).chunks}
    for row in rows:
        if row["in_corpus"] and row["source"] not in files:
            raise ValueError(f"{row['id']}: unknown playbook file {row['source']}")
        if not row["in_corpus"] and row["source"] is not None:
            raise ValueError(f"{row['id']}: out-of-corpus question must not have a source")
    return rows


async def evaluate(rows: list[dict], search: PlaybookSearch | None = None) -> dict:
    router = Router(None)
    search = search or PlaybookSearch(PlaybookIndex.load())
    routing, top1, top3, refused, false_refusals, failures, degraded = 0, 0, 0, 0, 0, [], False
    inside = [row for row in rows if row["in_corpus"]]
    outside = [row for row in rows if not row["in_corpus"]]
    for row in rows:
        decision = await router.route(row["text"])
        routed = decision.case in row.get("accept", [row["case"]])
        routing += routed
        found = await search.search(row["text"], 3)
        degraded = degraded or found.degraded
        files = [hit.chunk.file for hit in found.hits]
        problem = None if routed else "routing"
        if row["in_corpus"]:
            top1 += files[:1] == [row["source"]]
            top3 += row["source"] in files
            false_refusals += not found.answerable
            if row["source"] not in files:
                problem = "source_not_in_top3"
            elif not found.answerable:
                problem = "false_refusal"
        else:
            refused += not found.answerable
            if found.answerable:
                problem = "answered_out_of_corpus"
        if problem:
            # IDs and classes only: no question text and no playbook text in the report.
            failures.append({"id": row["id"], "problem": problem})
    return {
        "total": len(rows),
        "in_corpus": len(inside),
        "out_of_corpus": len(outside),
        "search_mode": search.mode,
        "degraded_to_bm25": degraded,
        "fragments": len(search.index.chunks),
        "commit": search.index.commit,
        "routing_accuracy": round(routing / len(rows), 4),
        "source_top1": round(top1 / len(inside), 4),
        "source_top3": round(top3 / len(inside), 4),
        "false_refusals": false_refusals,
        "out_of_corpus_refused": round(refused / len(outside), 4) if outside else None,
        "failures": failures,
        "llm": "not used",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/h3/cases.jsonl")
    parser.add_argument("--mode", choices=["bm25", "vector", "rrf"], default="bm25")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    index = PlaybookIndex.load()
    if args.mode == "bm25":
        search = PlaybookSearch(index)
    else:
        search = create_search(Settings(rag_search_mode=args.mode), index)
    report = asyncio.run(evaluate(load(args.dataset, index), search))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output = args.output or ROOT / f"evals/h3/{args.mode}-report.json"
    output.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
