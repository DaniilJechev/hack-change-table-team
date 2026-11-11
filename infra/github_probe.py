"""Read-only GitHub probe through the agent's configured MCP; never starts Telegram polling."""

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


async def probe(user_id):
    from buddy.access.checker import AccessChecker
    from buddy.brief.collectors import github
    from buddy.data.backend import BackendData
    from buddy.data.base import DataError
    from buddy.demo.controller import DATA
    from buddy.executors.context import Context
    from buddy.mcp_hub.config import load_servers
    from buddy.mcp_hub.errors import MCPError
    from buddy.mcp_hub.fixtures import FixtureStore
    from buddy.mcp_hub.hub import Hub

    if not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        raise RuntimeError("Set GITHUB_PERSONAL_ACCESS_TOKEN in .env and recreate agent")
    data = BackendData(os.environ["BACKEND_URL"], os.environ["BUDDY_SERVICE_KEY"])
    try:
        profile = await data.profile(user_id)
        login = (profile.get("logins") or {}).get("github")
        if not login:
            raise RuntimeError("Set this user's GitHub login in GITHUB_USER_LOGINS and recreate backend")
        catalog = await data.access_catalog(user_id)
        obj = next(
            (
                item
                for item in catalog
                if item["instrument"] == "github" and not item.get("forbidden") and item.get("target")
            ),
            None,
        )
        if obj is None:
            raise RuntimeError("Configure GITHUB_REPOSITORY/team/catalog object and run make seed")
        servers = load_servers(Path(".env"))
        hub = Hub(
            {"github": servers["github"]},
            FixtureStore(DATA / "fixtures"),
            demo_mode=False,
            audit_sink=data.record_mcp_call,
        )
        context = Context(user_id=user_id, team_id=profile["team_id"], role=profile["role"])
        runtime = type("Runtime", (), {"gateway": hub})()
        since = datetime.now(UTC) - timedelta(days=1)
        result = await github(context, runtime, obj["target"], login.casefold(), since)
        print(f"GitHub H0: read-only MCP OK; {len(result.items)} personal changes in the last 24h")
        try:
            member = await AccessChecker(hub, None, lambda _: None).membership("access_status", obj, profile)
        except MCPError:
            print("GitHub H1: collaborator roster unavailable; check token owner's repository privileges")
        else:
            print(
                "GitHub H1: "
                + {
                    True: "collaborator found",
                    False: "not a collaborator",
                    None: "unknown (incomplete response)",
                }[member]
            )
    except DataError:
        raise RuntimeError(
            "Backend profile/catalog unavailable; check user ID, configuration and seed"
        ) from None
    except MCPError:
        raise RuntimeError("GitHub MCP read failed; check PAT, repository access and connection") from None
    finally:
        await data.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose")
    parser.add_argument("--user", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        asyncio.run(probe(args.user))
    else:
        subprocess.run(
            [
                *shlex.split(args.compose),
                "exec",
                "-T",
                "agent",
                "python",
                "-",
                "--inside",
                "--user",
                args.user,
            ],
            input=Path(__file__).read_text(),
            text=True,
            check=True,
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, RuntimeError) else "check Compose services/configuration"
        print("GitHub check failed: " + message, file=sys.stderr)
        sys.exit(1)
