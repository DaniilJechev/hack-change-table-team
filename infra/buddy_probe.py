"""Check our real buddy-mcp from the agent container, without corporate services."""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path


async def probe():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    endpoint = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/") + "/mcp"
    headers = {
        "X-Buddy-Key": os.environ.get("BUDDY_SERVICE_KEY", ""),
        "X-Buddy-User": "demo-daniil",
    }
    expected = {
        "user_profile_get",
        "team_card_get",
        "team_faq_search",
        "access_catalog_get",
        "access_marks_get",
        "interns_list",
    }
    async with (
        httpx.AsyncClient(headers=headers, timeout=5, trust_env=False) as client,
        streamable_http_client(endpoint, http_client=client) as (read, write, _),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if {tool.name for tool in tools.tools} != expected:
                raise RuntimeError("Unexpected buddy-mcp tools")
            if any(not tool.annotations or not tool.annotations.readOnlyHint for tool in tools.tools):
                raise RuntimeError("buddy-mcp advertised a tool without readOnlyHint")
            card = await session.call_tool("team_card_get", {"section": "people"})
            if card.isError:
                raise RuntimeError("Cannot read own team card; run make seed")
            payload = json.loads(card.content[0].text)
            if payload["team_id"] != "demo-orders" or not payload["document"]["people"]:
                raise RuntimeError("Unexpected team card")
            for name, arguments in (
                ("user_profile_get", {}),
                ("team_faq_search", {"text": "наставник"}),
                ("access_catalog_get", {}),
                ("access_marks_get", {}),
            ):
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"Cannot call {name}")
            forbidden = await session.call_tool("interns_list", {})
            if not forbidden.isError:
                raise RuntimeError("interns_list allowed for an intern")
    print("buddy-mcp: six read tools; team card received by the agent; intern scope checked")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.inside:
        asyncio.run(probe())
    else:
        subprocess.run(
            shlex.split(options.compose) + ["exec", "-T", "agent", "python", "-", "--inside"],
            input=Path(__file__).read_text(),
            text=True,
            check=True,
        )


if __name__ == "__main__":
    main()
