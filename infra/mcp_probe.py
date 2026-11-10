# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp==1.26.0"]
# ///
"""Inventory MCP tools via the official SDK; never calls corporate tools."""

import argparse
import asyncio
import json
import shlex
import subprocess
from pathlib import Path

from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from mcp import ClientSession, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


async def inventory(read, write, expected):
    async with ClientSession(read, write) as session:
        await session.initialize()
        names = set()
        cursor = None
        while True:
            result = await session.list_tools(cursor=cursor)
            names.update(tool.name for tool in result.tools)
            cursor = result.nextCursor
            if not cursor:
                break
        if names != set(expected):
            raise RuntimeError(
                f"Unexpected inventory: missing={sorted(set(expected) - names)}, "
                f"extra={sorted(names - set(expected))}"
            )
        return sorted(names)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="docker compose -f docker-compose.yml -f docker-compose.mcp.yml")
    parser.add_argument("--only", choices=["github", "atlassian", "calendar"])
    arguments = parser.parse_args()
    compose = shlex.split(arguments.compose)
    expected = json.loads((ROOT / "mcp/tools.json").read_text())
    for name in expected:
        if arguments.only and name != arguments.only:
            continue
        async with asyncio.timeout(45):
            if name == "github":
                invocation = [*compose, "exec", "-T", "agent", "python", "/app/github_mcp.py"]
                parameters = StdioServerParameters(command=invocation[0], args=invocation[1:])
                async with stdio_client(parameters) as (read, write):
                    names = await inventory(read, write, expected[name])
            else:
                service, port, path = (
                    ("mcp-atlassian", "8000", "/mcp/")
                    if name == "atlassian"
                    else ("google-calendar-mcp", "3000", "/mcp")
                )
                address = subprocess.check_output(
                    [*compose, "port", service, port], text=True, timeout=10
                ).strip()
                headers = {}
                if name == "atlassian":
                    # Read credentials directly from the server environment; never print them.
                    encoded = subprocess.check_output(
                        [
                            *compose,
                            "exec",
                            "-T",
                            service,
                            "python",
                            "-c",
                            "import os,base64; print(base64.b64encode("
                            "(os.environ['JIRA_USERNAME']+':'+os.environ['JIRA_API_TOKEN']).encode()).decode())",
                        ],
                        text=True,
                        timeout=10,
                    ).strip()
                    headers["Authorization"] = "Basic " + encoded
                async with streamablehttp_client(f"http://{address}{path}", headers=headers) as (
                    read,
                    write,
                    _,
                ):
                    names = await inventory(read, write, expected[name])
        print(f"NFR-MCP-BOOT-1 {name}: {', '.join(names)}")


if __name__ == "__main__":
    asyncio.run(main())
