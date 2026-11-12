"""Offline freeze acceptance: full Telegram scenario and an audit of all replayed MCP calls.

Runs without Telegram polling, LLM credentials or corporate network requests. The audit is
explicitly from synthetic fixtures, not evidence of access to a real corporate installation.
"""

import asyncio
import json
import runpy
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def probe():
    sys.path.insert(0, str(ROOT / "agent"))
    from buddy.demo.controller import DemoController
    from buddy.mcp_hub.errors import ToolDenied
    from buddy.mcp_hub.policy import READ_TOOLS

    hubs = []
    original = DemoController.hub

    def capture(controller, user):
        hub = original(controller, user)
        hubs.append(hub)
        return hub

    DemoController.hub = capture
    try:
        scope = runpy.run_path(
            str(ROOT / "scripts/demo_transcript.py"), run_name="freeze_acceptance"
        )
        transcript = await scope["run"]()
        assert (
            "на согласовании" in transcript and "Спасибо за оценку брифа" in transcript
        )
        demo = DemoController(True)
        demo.command(1)
        hub = demo.hub(1)
        try:
            await hub.call(
                "access_status", "miro", "board_share", {}, user_id="demo-daniil"
            )
        except ToolDenied:
            pass
        else:
            raise AssertionError("External write tool was accepted")
    finally:
        DemoController.hub = original
    records = [record for hub in hubs for record in hub.audit]
    assert records
    successful = [record for record in records if record["outcome"] == "ok"]
    assert successful
    writes = [
        record
        for record in successful
        if record["tool"] not in READ_TOOLS.get(record["server"], ())
    ]
    assert not writes
    assert any(
        record["tool"] == "board_share" and record["outcome"] == "tool_denied"
        for record in records
    )
    assert all(
        set(record) == {"case", "server", "tool", "user_id", "outcome"}
        for record in records
    )
    print(
        json.dumps(
            {
                "source": "synthetic_offline_replay",
                "scenario": "passed",
                "mcp_calls": len(records),
                "successful_reads": len(successful),
                "successful_external_writes": len(writes),
                "denied_attempts": sum(
                    record["outcome"] == "tool_denied" for record in records
                ),
                "calls_by_server": dict(
                    Counter(record["server"] for record in records)
                ),
                "telegram_polling": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(probe())
