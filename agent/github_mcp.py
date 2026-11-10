"""Infrastructure entrypoint for read-only GitHub stdio; no Docker socket needed."""

import os

os.execve(
    "/usr/local/bin/github-mcp-server",
    [
        "github-mcp-server",
        "stdio",
        "--read-only",
        "--tools=get_me,list_pull_requests,search_pull_requests,pull_request_read,"
        "list_notifications,list_repository_collaborators,get_team_members",
    ],
    {"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")},
)
