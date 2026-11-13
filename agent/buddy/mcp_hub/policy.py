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
    "buddy": frozenset(
        {
            "user_profile_get",
            "team_card_get",
            "team_faq_search",
            "access_catalog_get",
            "access_marks_get",
            "interns_list",
        }
    ),
    "telegram": frozenset({"getChatMember"}),
    "miro": frozenset({"board_search_boards", "content_item_list_roles"}),
    # H4: mcp/powerpoint/entrypoint.py removes every other upstream tool before start.
    "powerpoint": frozenset({"open_presentation", "extract_presentation_text"}),
}
PROBE_TOOLS = {
    "github": "list_pull_requests",
    "jira": "jira_get_issue",
    "calendar": "list-events",
    "miro": "content_item_list_roles",
    "powerpoint": "extract_presentation_text",
}

# Case capabilities are enforced both at the executor boundary and in Hub.
CASE_TOOLS = {"stage1_probe": {name: frozenset({tool}) for name, tool in PROBE_TOOLS.items()}}
BRIEF_TOOLS = {
    "github": READ_TOOLS["github"] - {"get_me", "list_repository_collaborators", "get_team_members"},
    "jira": frozenset({"jira_search", "jira_get_issue"}),
    "calendar": frozenset({"list-events"}),
}
ACCESS_TOOLS = {
    "buddy": frozenset({"access_catalog_get", "access_marks_get", "user_profile_get"}),
    "github": frozenset({"list_repository_collaborators", "get_team_members"}),
    "jira": frozenset({"jira_search_assignable_users"}),
    "miro": frozenset({"content_item_list_roles"}),
    "calendar": frozenset({"list-events"}),
    "telegram": frozenset({"getChatMember"}),
}
for name in ("brief_now", "brief_render"):
    CASE_TOOLS[name] = dict(BRIEF_TOOLS)
for name in ("access_status", "access_events", "access_task_analysis", "interns_status"):
    CASE_TOOLS[name] = dict(ACCESS_TOOLS)
CASE_TOOLS["interns_status"]["buddy"] |= {"interns_list"}
CASE_TOOLS["access_task_analysis"]["jira"] |= {"jira_get_issue"}
for number in range(1, 9):
    CASE_TOOLS[f"team_q{number}"] = {"buddy": frozenset({"team_card_get", "team_faq_search"})}
for name in ("team_q5", "team_q6"):
    CASE_TOOLS[name] = {
        "buddy": frozenset({"team_card_get"}),
        "jira": frozenset({"jira_get_issue", "jira_search"}),
    }
CASE_TOOLS["team_q8"].update(ACCESS_TOOLS)
CASE_TOOLS["team_q8"]["buddy"] |= {"team_card_get", "team_faq_search"}
CASE_TOOLS["equator_check"] = {
    "jira": frozenset({"jira_get_issue"}),
    "powerpoint": frozenset({"open_presentation", "extract_presentation_text"}),
}
for name in ("playbook_qa", "help", "clarify", "out_of_scope"):
    CASE_TOOLS[name] = {}


def permitted(case: str, server: str, tool: str) -> None:
    if tool not in CASE_TOOLS.get(case, {}).get(server, ()) or tool not in READ_TOOLS.get(server, ()):
        raise ToolDenied("Tool is not allowed for this case")


def is_read_tool(server: str, tool: dict) -> bool:
    return (
        tool.get("name") in READ_TOOLS.get(server, ())
        and (tool.get("annotations") or {}).get("readOnlyHint") is not False
    )
