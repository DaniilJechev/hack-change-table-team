"""Dialog memory evaluation: multi-turn follow-ups through AgentService in demo mode.

Requirements: FR-MEM-03/04/05 (context-memory), FR-H0-12 (brief follow-ups), FR-H3-07 (playbook
follow-ups), RT-08 (Router with memory). Each dialog runs on a fresh service with fixtures only:
rules only, no LLM, no network, no database. Control turns emulate the bot: `/reset` calls
`AgentService.reset`, `/demo <action>` clears the demo session memory like `bot/stage_two.py`.

Turn checks (all optional except `case`): `params` (exact subset), `params_contain` (substring per
key), `params_present` (keys), `link_contains`, `link_not_contains`, `links_all_contain`, `links_empty`,
`result_contains`, `result_not_contains`, `action_label`, `no_search` (playbook index not searched),
`no_interns_call` (the mentor-only interns summary is not executed).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from buddy.config import Settings
from buddy.router.service import Router
from buddy.service import AgentService

ROOT = Path(__file__).resolve().parents[1]
SESSION = 1
DEMO_ACTIONS = {"", "next", "reset", "off", "mentor", "intern"}


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Dataset needs unique IDs and at least one dialog")
    for row in rows:
        if row.get("role", "intern") not in {"intern", "mentor"} or not row.get("turns"):
            raise ValueError(f"{row['id']}: role must be intern|mentor and turns must not be empty")
        for turn in row["turns"]:
            if not turn["text"].startswith(("/reset", "/demo")) and "case" not in turn:
                raise ValueError(f"{row['id']}: turn {turn['text']!r} needs an expected case")
    return rows


class Counter:
    """Counts calls of an async method without changing its behaviour."""

    def __init__(self, owner, name: str):
        self.calls = 0
        original = getattr(owner, name)

        async def wrapped(*args, **kwargs):
            self.calls += 1
            return await original(*args, **kwargs)

        setattr(owner, name, wrapped)


def check(turn: dict, decision, response, searches: int, interns: int) -> list[str]:
    problems = []
    links, result, params = response.links, response.result, decision.params
    if decision.case != turn["case"]:
        problems.append(f"case {decision.case} != {turn['case']}")
    for name, value in turn.get("params", {}).items():
        if params.get(name) != value:
            problems.append(f"params.{name}={params.get(name)!r} != {value!r}")
    for name, value in turn.get("params_contain", {}).items():
        if value not in (params.get(name) or ""):
            problems.append(f"params.{name} lacks {value!r}")
    for name in turn.get("params_present", []):
        if not params.get(name):
            problems.append(f"params.{name} missing")
    if "link_contains" in turn and not any(turn["link_contains"] in link for link in links):
        problems.append(f"no link with {turn['link_contains']!r}")
    if "link_not_contains" in turn and any(turn["link_not_contains"] in link for link in links):
        problems.append(f"unexpected link with {turn['link_not_contains']!r}")
    if "links_all_contain" in turn and (not links or not all(turn["links_all_contain"] in x for x in links)):
        problems.append(f"links not all {turn['links_all_contain']!r}: {len(links)} links")
    if turn.get("links_empty") and (links or "http" in result):
        problems.append(f"links must be empty, got {len(links)}")
    if "result_contains" in turn and turn["result_contains"] not in result:
        problems.append(f"result lacks {turn['result_contains']!r}")
    if "result_not_contains" in turn and turn["result_not_contains"] in result:
        problems.append(f"result has {turn['result_not_contains']!r}")
    if "action_label" in turn and turn["action_label"] not in [action.label for action in response.actions]:
        problems.append(f"no button {turn['action_label']!r}")
    if turn.get("no_search") and searches:
        problems.append(f"playbook search called {searches}x")
    if turn.get("no_interns_call") and interns:
        problems.append(f"interns summary executed {interns}x")
    return problems


async def run_dialog(row: dict) -> list[dict]:
    service = AgentService(Router(), demo_mode=True, settings=Settings(_env_file=None, agent_engine="graph"))
    service.demo.command(SESSION, "mentor" if row.get("role") == "mentor" else "")
    context = service.demo.context(SESSION)
    search = Counter(service.executors.playbook.search, "search")
    interns = Counter(service.executors.access, "interns")
    results = []
    for index, turn in enumerate(row["turns"]):
        if row.get("reset_before") == index:
            await service.reset(context)
        text = turn["text"]
        search.calls = interns.calls = 0
        problems: list[str] = []
        case = params = None
        links: list[str] = []
        try:
            if text.strip() == "/reset":
                response = await service.reset(context)
                case = "/reset"
                if "result_contains" in turn and turn["result_contains"] not in response.result:
                    problems.append(f"result lacks {turn['result_contains']!r}")
            elif text.startswith("/demo"):
                action = text[len("/demo") :].strip()
                if action not in DEMO_ACTIONS:
                    raise ValueError(f"unknown demo action {action!r}")
                # Same as bot/stage_two.py: restart, off and role switch clear the session memory.
                if action in {"reset", "off", "mentor", "intern"} or not service.demo.active(SESSION):
                    service.clear_demo(SESSION)
                service.demo.command(SESSION, action)
                context = service.demo.context(SESSION)
                case = text
            else:
                decision, response = await service.handle(text, context)
                case, params, links = decision.case, dict(decision.params), list(response.links)
                problems = check(turn, decision, response, search.calls, interns.calls)
        except Exception as error:  # noqa: BLE001 — an unimplemented path fails the turn, not the run
            problems.append(f"error {type(error).__name__}: {error}")
        results.append(
            {
                "dialog": row["id"],
                "turn": index + 1,
                "text": text,
                "expected": turn.get("case", "-"),
                "case": case,
                "params": params,
                "links": len(links),
                "ok": not problems,
                "problems": problems,
            }
        )
    return results


async def evaluate(rows: list[dict]) -> dict:
    turns = []
    for row in rows:
        turns.extend(await run_dialog(row))
    dialogs = []
    for row in rows:
        own = [turn for turn in turns if turn["dialog"] == row["id"]]
        dialogs.append(
            {
                "id": row["id"],
                "role": row.get("role", "intern"),
                "spec": row.get("spec", ""),
                "turns": len(own),
                "ok": all(turn["ok"] for turn in own),
            }
        )
    passed = sum(turn["ok"] for turn in turns)
    return {
        "dialogs": len(rows),
        "dialogs_passed": sum(dialog["ok"] for dialog in dialogs),
        "turns": len(turns),
        "turns_passed": passed,
        "pass_rate": round(passed / len(turns), 4) if turns else 0.0,
        "per_dialog": dialogs,
        "failures": [turn for turn in turns if not turn["ok"]],
        "results": turns,
        "llm": "not used",
    }


def table(report: dict) -> str:
    lines = [f"{'dialog':<8} {'#':>2} {'ok':<4} {'expected':<22} {'actual':<22} text"]
    for turn in report["results"]:
        lines.append(
            f"{turn['dialog']:<8} {turn['turn']:>2} {'ok' if turn['ok'] else 'FAIL':<4} "
            f"{turn['expected']:<22} {turn['case']!s:<22} {turn['text']}"
        )
        lines.extend(f"{'':<12}- {problem}" for problem in turn["problems"])
    lines.append(
        f"\nDialogs: {report['dialogs_passed']}/{report['dialogs']}, "
        f"turns: {report['turns_passed']}/{report['turns']} ({report['pass_rate']:.0%})"
    )
    return "\n".join(lines)


def markdown(report: dict) -> str:
    lines = [
        "# Оценка памяти диалога (демо-режим, только правила)",
        "",
        "Требования: FR-MEM-03/04/05, FR-H0-12, FR-H3-07, RT-08. LLM не вызывается; данные синтетические.",
        "Запуск: `uv run --project agent --frozen --extra dev python evals/run_memory.py`.",
        "",
        f"- Диалогов: {report['dialogs']}, пройдено: {report['dialogs_passed']}",
        f"- Ходов: {report['turns']}, пройдено: {report['turns_passed']} ({report['pass_rate']:.0%})",
        "",
        "| Диалог | Роль | Требование | Ходов | Результат |",
        "|---|---|---|---|---|",
    ]
    for dialog in report["per_dialog"]:
        status = "пройден" if dialog["ok"] else "**ошибка**"
        lines.append(
            f"| {dialog['id']} | {dialog['role']} | {dialog['spec']} | {dialog['turns']} | {status} |"
        )
    lines += ["", "## Ошибки", ""]
    if not report["failures"]:
        lines.append("Нет.")
    for turn in report["failures"]:
        problems = "; ".join(turn["problems"])
        lines.append(
            f"- {turn['dialog']}, ход {turn['turn']} «{turn['text']}»: ожидался `{turn['expected']}`, "
            f"получен `{turn['case']}` — {problems}"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/memory/cases.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/memory/report.md")
    parser.add_argument("--json", type=Path, nargs="?", const=Path("-"), help="dump results (default stdout)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(evaluate(load(args.dataset)))
    print(table(report))
    args.output.write_text(markdown(report), encoding="utf-8", newline="\n")
    if args.json is not None:
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if str(args.json) == "-":
            print(text)
        else:
            args.json.write_text(text, encoding="utf-8", newline="\n")
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
