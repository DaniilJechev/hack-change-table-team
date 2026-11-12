"""Personal Jira history from the pinned mcp-atlassian read tools; no view-history writes."""

import json
import re
from datetime import timedelta

from buddy.brief.collectors import call, item_id, moment, rows
from buddy.brief.models import BriefItem
from buddy.mcp_hub.errors import SourceUnavailable

FIELDS = "summary,status,assignee,duedate,priority,created,updated,comment"
CHANGES = {
    "status": ("jira_status_changed", "сменился статус", "info"),
    "assignee": ("jira_reassigned", "сменился исполнитель", "people"),
    "duedate": ("jira_due_changed", "изменился срок", "deadline"),
    "priority": ("jira_priority_changed", "изменился приоритет", "info"),
    "description": ("jira_description_changed", "изменилось описание", "info"),
}


def matches(user, login):
    if not isinstance(user, dict):
        return str(user or "").casefold() == login
    return any(
        str(user.get(key) or "").casefold() == login
        for key in ("account_id", "accountId", "name", "key", "email", "emailAddress", "login")
    )


def decode(value):
    if isinstance(value, dict) and isinstance(value.get("result"), str):
        try:
            value = json.loads(value["result"])
        except ValueError:
            raise SourceUnavailable("Invalid Jira response") from None
    if not isinstance(value, dict):
        raise SourceUnavailable("Invalid Jira response")
    return value


def quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def histories(issue):
    return rows(issue.get("changelogs", issue.get("changelog", [])), "histories", "values")


def in_period(value, since, now):
    at = moment(value)
    return at if at and since < at <= now else None


def value_of(change, side):
    # MCP simplified names first, with raw API names accepted for recorded responses.
    return change.get(f"{side}_string", change.get(f"{side}String"))


def was_mine(issue, login, since, now):
    return any(
        str(change.get("field", "")).casefold() == "assignee"
        and any(
            str(value or "").casefold() == login
            for value in (change.get("from_id", change.get("from")), value_of(change, "from"))
        )
        for history in histories(issue)
        if in_period(history.get("created"), since, now)
        for change in rows(history.get("items"))
    )


async def issues(context, runtime, target, login, since, now):
    project = target["project_key"]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", project):
        raise SourceUnavailable("Invalid Jira project")
    # Date-only JQL uses the Jira account's zone. Include an extra day and enforce exact times below.
    day = (since - timedelta(days=1)).strftime("%Y-%m-%d")
    user = quote(login)
    personal = f'(assignee = {user} OR assignee WAS {user} AFTER "{day}" OR comment ~ {user})'
    jql = (
        f"project = {quote(project)} AND {personal} AND "
        f'(updated >= "{day}" OR (assignee = {user} AND duedate <= 7d)) ORDER BY updated DESC'
    )
    found, seen_keys, seen_tokens = [], set(), set()
    page = {"start_at": 0}
    for _ in range(10):
        value = decode(
            await call(
                runtime,
                context,
                "jira",
                "jira_search",
                {
                    "jql": jql,
                    "fields": FIELDS,
                    "limit": 50,
                    **page,
                },
            )
        )
        if not isinstance(value.get("issues"), list):
            raise SourceUnavailable("Invalid Jira search")
        batch = rows(value["issues"])
        for candidate in batch:
            key = candidate.get("key", "")
            if not isinstance(key, str) or not re.fullmatch(re.escape(project) + r"-\d+(?:-\d+)*", key):
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            issue = decode(
                await call(
                    runtime,
                    context,
                    "jira",
                    "jira_get_issue",
                    {
                        "issue_key": key,
                        "fields": FIELDS,
                        "include": "changelog,comments",
                        "comment_limit": 100,
                        "update_history": False,
                    },
                )
            )
            if issue.get("key") != key:
                raise SourceUnavailable("Invalid Jira issue")
            if not isinstance(issue.get("changelogs"), list) or not isinstance(issue.get("comments"), list):
                raise SourceUnavailable("Incomplete Jira issue")
            issue = dict(issue)
            issue["url"] = issue.get("browse_url") or issue.get("url") or ""
            issue["summary"] = str(issue.get("summary") or "")[:100]
            issue["_mine"] = matches(issue.get("assignee"), login)
            issue["_was_mine"] = was_mine(issue, login, since, now)
            found.append(issue)
        token = value.get("next_page_token")
        if token:
            if not isinstance(token, str) or token in seen_tokens or not batch:
                raise SourceUnavailable("Invalid Jira pagination")
            seen_tokens.add(token)
            page = {"page_token": token}
        elif value.get("total", -1) >= 0:
            end = page.get("start_at", 0) + len(batch)
            if end >= value["total"]:
                return found
            if not batch:
                raise SourceUnavailable("Incomplete Jira search")
            page = {"start_at": end}
        else:
            return found  # Cloud omits total and signals the final page by omitting its token.
    raise SourceUnavailable("Jira search page limit exceeded")


def changes(context, issue, login, since, now):
    if not (issue["_mine"] or issue["_was_mine"]):
        return []
    result = {}
    key = issue["key"]
    for history in histories(issue):
        at = in_period(history.get("created"), since, now)
        if not at:
            continue
        for change in rows(history.get("items")):
            field = str(change.get("field", "")).casefold()
            if field not in CHANGES:
                continue
            before, after = value_of(change, "from"), value_of(change, "to")
            identity = item_id(
                context.user_id,
                "jira",
                key,
                at.isoformat(),
                field,
                before,
                after,
                change.get("from_id"),
                change.get("to_id"),
            )
            kind, label, priority = CHANGES[field]
            if field == "assignee" and any(
                str(value or "").casefold() == login
                for value in (change.get("to_id", change.get("to", "")), after)
            ):
                kind, label = "jira_assigned", "задача назначена вам"
            # Description may be long or unavailable; never include its contents in the brief/prompt.
            show_values = field != "description" and before is not None and after is not None
            result[identity] = BriefItem(
                id=identity,
                source="jira",
                type=kind,
                title=f"{key} «{issue['summary']}» — {label}",
                url=issue["url"],
                priority_class=priority,
                related_task=key,
                created_at=at,
                before=str(before)[:100] if show_values else "",
                after=str(after)[:100] if show_values else "",
            )
    updated = in_period(issue.get("updated"), since, now)
    if not result and updated and not in_period(issue.get("created"), since, now):
        identity = item_id(context.user_id, "jira", key, "updated", updated.isoformat())
        result[identity] = BriefItem(
            id=identity,
            source="jira",
            type="jira_updated",
            title=f"{key} «{issue['summary']}» — задача обновлена",
            url=issue["url"],
            priority_class="info",
            related_task=key,
            created_at=updated,
        )
    return list(result.values())
