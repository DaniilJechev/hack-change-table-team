from buddy.mcp_hub.errors import ToolDenied

# Reviewed candidates from project architecture. Live discovery must independently confirm each name/schema.
READ_TOOLS = {
    "github": frozenset(
        {
            "get_me",
            "list_pull_requests",
            "search_pull_requests",
            "pull_request_read",
            "list_notifications",
            "list_repository_collaborators",
            "get_team_members",
        }
    ),
    "jira": frozenset(
        {
            "jira_search",
            "jira_get_issue",
            "jira_search_assignable_users",
            "jira_get_user_profile",
            "jira_get_all_projects",
        }
    ),
    "calendar": frozenset(
        {"list-events", "search-events", "get-event", "list-calendars", "get-freebusy", "get-current-time"}
    ),
    "miro": frozenset({"board_search_boards", "content_item_list_roles"}),
}
PROBE_TOOLS = {
    "github": "list_pull_requests",
    "jira": "jira_get_issue",
    "calendar": "list-events",
    "miro": "content_item_list_roles",
}

# Only a diagnostic entry point is implemented now; this is not a Router/Executor implementation.
CASE_TOOLS = {"stage1_probe": {name: frozenset({tool}) for name, tool in PROBE_TOOLS.items()}}


def permitted(case: str, server: str, tool: str) -> None:
    if tool not in CASE_TOOLS.get(case, {}).get(server, ()) or tool not in READ_TOOLS.get(server, ()):
        raise ToolDenied("Tool is not allowed for this case")


def is_read_tool(server: str, tool: dict) -> bool:
    return (
        tool.get("name") in READ_TOOLS.get(server, ())
        and (tool.get("annotations") or {}).get("readOnlyHint") is not False
    )
