"""Synthetic SDK server for protocol integration tests; never an official-service substitute."""

import argparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

parser = argparse.ArgumentParser()
parser.add_argument("--http", action="store_true")
parser.add_argument("--port", type=int, default=8769)
args = parser.parse_args()
server = FastMCP("synthetic-sdk-test-server", host="127.0.0.1", port=args.port, stateless_http=True)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_pull_requests(owner: str, repo: str, state: str = "open", perPage: int = 10) -> dict:
    return {"synthetic": True, "owner": owner, "repo": repo, "state": state, "perPage": perPage}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def jira_get_issue(issue_key: str) -> dict:
    if issue_key == "ERROR":
        raise ValueError("synthetic-error-not-for-journal")
    return {"synthetic": True, "issue_key": issue_key}


@server.tool(name="list-events", annotations=ToolAnnotations(readOnlyHint=True))
def list_events(calendarId: str, timeMin: str, timeMax: str) -> dict:
    return {"synthetic": True, "calendarId": calendarId, "timeMin": timeMin, "timeMax": timeMax}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def content_item_list_roles(contentItemId: str) -> dict:
    return {"synthetic": True, "contentItemId": contentItemId, "roles": []}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False))
def board_share(contentItemId: str) -> dict:
    raise AssertionError("A write tool must never be invoked")


server.run(transport="streamable-http" if args.http else "stdio")
