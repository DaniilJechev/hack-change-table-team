"""Opt-in acceptance against an isolated disposable Compose project.

Runs the real backend/PostgreSQL and official MCP discovery. Telegram uses an
OfflineSession, and corporate tools/call are deliberately not invoked.
Usage: python seed/check_stage_one.py --project buddy-stage1-acceptance
Run once per fresh project volume: the invitation is single-use.
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(*args, stdin=None):
    result = subprocess.run(
        ["docker", *args],
        check=False,
        input=stdin,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        # May contain invitation codes/credentials: do not propagate captured output.
        raise RuntimeError(
            "Acceptance subprocess failed; inspect the local service health"
        )
    return result.stdout.strip()


def container(project, service):
    ids = command(
        "ps",
        "-q",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        f"label=com.docker.compose.service={service}",
    ).splitlines()
    if len(ids) != 1:
        raise RuntimeError("Expected one running acceptance service")
    return ids[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="buddy-stage1-acceptance")
    args = parser.parse_args()
    # Guard against accidentally consuming invitations in the live development stack.
    if not args.project.startswith("buddy-stage1-acceptance"):
        parser.error("Use an isolated buddy-stage1-acceptance project")
    backend, agent = (
        container(args.project, service) for service in ("backend", "agent")
    )
    command("exec", backend, "backend", "seed")
    invitation = command("exec", backend, "backend", "invite", "--user", "demo-daniil")
    code = r"""import asyncio, json, os
from datetime import UTC, datetime
from pathlib import Path
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User
from buddy.bot.handlers import create_dispatcher
from buddy.bot.identity import IdentityClient, IdentityError
from buddy.smoke import OfflineSession
from buddy.mcp_hub.config import load_servers
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.hub import Hub

async def main():
    identity = IdentityClient(os.environ["BACKEND_URL"], os.environ["BUDDY_SERVICE_KEY"])
    session = OfflineSession()
    bot = Bot(token="123456:SYNTHETIC_TEST_TOKEN", session=session)
    dispatcher = create_dispatcher(identity)
    try:
        for index, text in enumerate(["/start " + INVITATION, "/start", "/help"]):
            message = Message(message_id=index+1, date=datetime.now(UTC),
                chat=Chat(id=900001, type="private"),
                from_user=User(id=900001,is_bot=False,first_name="Synthetic"), text=text)
            await dispatcher.feed_update(bot, Update(update_id=index+1, message=message))
        assert len(session.sent) == 3
        assert all("demo-orders" in item.text for item in session.sent[:2])
        assert "Этап 1" in session.sent[2].text
        assert all(INVITATION not in item.text for item in session.sent)
        profile = await identity.profile(900001)
        assert profile.user_id == "demo-daniil" and profile.role == "intern"
        try:
            await identity.bind(INVITATION,900002,900002)
            raise AssertionError("Invitation replay unexpectedly succeeded")
        except IdentityError as error:
            assert error.kind == "invalid"
    finally:
        await identity.close()
        await bot.session.close()
    hub = Hub(load_servers(Path("/nonexistent.env")), FixtureStore(Path("/tmp/unwritten-fixtures")),
              demo_mode=False)
    catalogs = {}
    for server in ("github", "jira", "calendar"):
        result = await hub.discover(server)
        catalogs[server] = {"origin":"official-runtime-discovery", "credentials":"synthetic",
            "corporate_api_verified":False, "tools":result["tools"]}
    print(json.dumps({"identity":"passed", "telegram_transport":"offline", "catalogs":catalogs},
                     ensure_ascii=False))
asyncio.run(main())
"""
    result = json.loads(
        command(
            "exec",
            "-i",
            agent,
            "python",
            "-",
            stdin="INVITATION = " + repr(invitation) + "\n" + code,
        )
    )
    expected = json.loads((ROOT / "mcp/tools.json").read_text(encoding="utf-8"))
    out = ROOT / "mcp/discovery"
    out.mkdir(exist_ok=True)
    for server, catalog in result["catalogs"].items():
        names = {tool["name"] for tool in catalog["tools"]}
        assert names == set(expected["atlassian" if server == "jira" else server])
        (out / (server + ".json")).write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"{server}: official tools/list verified ({len(names)} tools), tools/call not attempted"
        )
    print(
        "ST1-BIND-1: real REST + PostgreSQL + offline Telegram dispatcher passed; replay rejected"
    )


if __name__ == "__main__":
    main()
