"""Stage-1 diagnostics, explicitly separate from the Telegram runtime."""

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values

from buddy.mcp_hub.config import SERVERS, load_servers
from buddy.mcp_hub.errors import ConfigurationError, MCPError
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.hub import Hub
from buddy.mcp_hub.oauth import EnvTokenStorage, browser_flow, configured_secrets, provider


def build_hub(args) -> Hub:
    values = dotenv_values(args.env_file, interpolate=False) if args.env_file.exists() else {}
    demo = not args.live
    record = args.record or str(values.get("RECORD_MODE", "false")).lower() == "true"
    configs = load_servers(args.env_file)
    auth = {}
    if not demo and configs["miro"].oauth:
        auth["miro"] = provider(configs["miro"].url, EnvTokenStorage(args.env_file))
    store = FixtureStore(args.fixtures, step=args.step, secrets=configured_secrets(args.env_file))
    return Hub(configs, store, demo_mode=demo, record_mode=record, auth=auth)


async def execute(args) -> tuple[dict, int]:
    hub = build_hub(args)
    if args.action == "auth-miro":
        config = hub.servers["miro"]
        async with browser_flow(config.url, EnvTokenStorage(args.env_file), port=args.port) as auth:
            hub.demo_mode = False
            hub.auth["miro"] = auth
            async with asyncio.timeout(240):
                catalog = await hub.discover("miro")
            return {
                "server": "miro",
                "status": "authorized",
                "tool_count": len(catalog["tools"]),
                "verified_live": True,
            }, 0
    if args.action == "discover":
        results = await hub.probe()
        success = all(result.get("required_available") for result in results)
        return {"mode": "offline" if hub.demo_mode else "live", "servers": results}, 0 if success else 1
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError
    except ValueError as exc:
        raise ConfigurationError("Tool arguments must be a JSON object") from exc
    await hub.call("stage1_probe", args.server, args.tool, arguments)
    # Responses can contain private source data: report only the outcome, never the raw content.
    return {
        "server": args.server,
        "tool": args.tool,
        "status": "ok",
        "mode": "offline" if hub.demo_mode else "live",
        "recorded": hub.record_mode,
        "audit": hub.audit,
    }, 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["discover", "call", "auth-miro"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    parser.add_argument("--step", default="step_1")
    parser.add_argument("--live", action="store_true", help="Opt in to real MCP connections")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--server", choices=SERVERS, default="github")
    parser.add_argument("--tool", default="list_pull_requests")
    parser.add_argument("--arguments", default="{}")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.action == "auth-miro" and not args.live:
        print(json.dumps({"status": "configuration_error", "hint": "OAuth requires explicit --live"}))
        return 2
    try:
        result, code = asyncio.run(execute(args))
    except MCPError as exc:
        result, code = {"status": exc.code}, 1
    except (OSError, TimeoutError):
        result, code = {"status": "source_unavailable"}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
