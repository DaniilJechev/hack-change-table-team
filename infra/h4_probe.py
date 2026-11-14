"""Exercise H4 through the deployed agent and real REST/PostgreSQL using a synthetic PDF.

Corporate MCP calls are replaced by recorded Jira fixtures. This probe never sends Telegram
messages or marks progress, but creates a synthetic check and invalidates its old send button.
Run after the updated agent/backend images and seed are deployed.
"""

import argparse
import base64
import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


async def agent_probe(payload):
    from buddy.config import Settings
    from buddy.data.backend import BackendData
    from buddy.demo.controller import DemoController
    from buddy.equator.service import EquatorService
    from buddy.executors.context import Context
    from buddy.runtime import Runtime

    settings = Settings()
    data = BackendData(
        settings.backend_url, settings.buddy_service_key.get_secret_value()
    )
    controller = DemoController(True)
    controller.command(991001)
    fixture_runtime = controller.runtime(991001)

    class SyntheticScope:
        """Only corporate source scope is synthetic; all history remains real REST."""

        def __init__(self, backend):
            self.backend = backend

        def __getattr__(self, name):
            return getattr(self.backend, name)

        async def access_catalog(self, user_id):
            return await fixture_runtime.data.access_catalog(user_id)

        def target(self, object_id):
            return fixture_runtime.data.target(object_id)
    try:
        profile = await data.profile("demo-daniil")
        context = Context(
            profile["user_id"], profile["team_id"], profile["role"], demo=False
        )
        runtime = Runtime(SyntheticScope(data), fixture_runtime.gateway, fixture_runtime.telegram)
        service = EquatorService(settings=settings)
        response, record = await service.check(
            "synthetic-h4-probe.pdf",
            base64.b64decode(payload["pdf"], validate=True),
            context,
            runtime,
            file_id="synthetic-probe-never-send",
        )
        assert record is not None, "H4 did not save the synthetic check"
        assert record.passed and response.actions, (
            "Synthetic good.pdf did not pass mandatory criteria: " + response.result
        )
        check_id = record.id
        created = datetime.fromisoformat(record.created_at)
        expires = datetime.fromisoformat(record.expires_at)
        # Backend's configured TTL is authoritative; agent and backend receive the same compose env.
        ttl_seconds = (expires - created).total_seconds()
        assert abs(ttl_seconds - settings.h4_history_ttl_days * 86400) < 2, (
            "Unexpected PostgreSQL history TTL"
        )

        # Replace both services: no EquatorService or BackendData process cache can supply history.
        await data.close()
        data = BackendData(
            settings.backend_url, settings.buddy_service_key.get_secret_value()
        )
        runtime = Runtime(SyntheticScope(data), fixture_runtime.gateway, fixture_runtime.telegram)
        restarted = EquatorService(settings=settings)
        restored = await restarted.records(context, runtime)
        saved = next((row for row in restored if row.id == check_id), None)
        assert saved is not None and saved.passed and saved.current, (
            "REST history did not restore check"
        )
        assert saved.file_id == "synthetic-probe-never-send", (
            "Telegram file_id was not persisted"
        )
        assert saved.result == json.loads(json.dumps(record.result)) and saved.links == record.links, (
            "Stored result changed"
        )

        # A later upload invalidates the successful version before any attempted delivery.
        await restarted.invalidate(context, runtime)
        reply, mentor, note = await restarted.send(check_id, context, runtime)
        assert mentor is None and note is None, (
            "A stale check still allows mentor delivery"
        )
        assert "устарела" in reply.result, (
            "Stale check did not return an actionable refusal"
        )
        print(
            "H4 probe OK: good PDF, real REST/PostgreSQL history, fresh-client restore, "
            f"TTL {settings.h4_history_ttl_days} days, stale send blocked; Telegram was not called"
        )
    finally:
        await data.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose")
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "fixtures/equator/good.pdf",
    )
    args = parser.parse_args()
    payload = {"pdf": base64.b64encode(args.pdf.read_bytes()).decode("ascii")}
    # Copy only this probe and the synthetic PDF over stdin. Credentials stay in container env.
    source = Path(__file__).read_text(encoding="utf-8")
    source = source[: source.index('\nif __name__ == "__main__":')]
    script = (
        source
        + "\nimport asyncio\nasyncio.run(agent_probe(json.loads("
        + repr(json.dumps(payload))
        + ")))\n"
    )
    result = subprocess.run(
        [*shlex.split(args.compose), "exec", "-T", "agent", "python", "-"],
        input=script.encode("utf-8"),
        check=False,
        timeout=120,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
