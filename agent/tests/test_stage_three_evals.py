"""Stage 3 eval sets (Daniil): H2 40 questions, H3 35 questions; committed reports are reproducible."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"evals/{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


async def test_st3_eval_h2_40_questions_8_types_reproducible():
    h2 = module("run_h2")
    rows = h2.load(ROOT / "evals/h2/cases.jsonl")
    assert len(rows) == 40 and Counter(row["case"] for row in rows) == {f"team_q{i}": 5 for i in range(1, 9)}
    report = await h2.evaluate(rows)
    committed = json.loads((ROOT / "evals/h2/rules-report.json").read_text(encoding="utf-8"))
    assert committed == report
    assert report["routing_accuracy"] == 1 and report["answers_with_links"] == 1  # H2 links ≥ 95 %
    assert report["answer_accuracy"] == 1


async def test_st3_eval_h3_35_questions_pinned_sources_reproducible(tmp_path):
    h3 = module("run_h3")
    rows = h3.load(ROOT / "evals/h3/cases.jsonl")
    assert len(rows) == 35 and sum(row["in_corpus"] for row in rows) == 30
    report = await h3.evaluate(rows)
    committed = json.loads((ROOT / "evals/h3/bm25-report.json").read_text(encoding="utf-8"))
    assert committed == report and report["routing_accuracy"] >= 0.9
    assert report["search_mode"] == "bm25" and not report["degraded_to_bm25"]
    assert report["source_top3"] >= 0.9  # stage-5 target: the source is in the top three
    assert report["out_of_corpus_refused"] >= 0.8 and report["false_refusals"] <= 1
    broken = tmp_path / "broken.jsonl"
    broken.write_text(json.dumps({**rows[0], "source": "missing.md"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown playbook file"):
        h3.load(broken)
    broken.write_text(json.dumps({**rows[-1], "source": "spt.md"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must not have a source"):
        h3.load(broken)
    broken.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="unique IDs"):
        h3.load(broken)


def test_st4_eval_h3_cli_writes_the_committed_bm25_report(tmp_path, capsys):
    output = tmp_path / "report.json"
    assert module("run_h3").main(["--output", str(output)]) == 0
    committed = json.loads((ROOT / "evals/h3/bm25-report.json").read_text(encoding="utf-8"))
    assert json.loads(output.read_text(encoding="utf-8")) == committed
    assert '"search_mode": "bm25"' in capsys.readouterr().out
