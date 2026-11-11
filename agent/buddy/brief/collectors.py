"""Read-only collectors of changes for the morning brief (FR-H0-01): GitHub, Jira, Google Calendar.

Every call goes through the Hub allowlist of the `brief_now` case. Tool arguments carry no time
filters: results are filtered by code against the moment of the last successfully sent brief.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from buddy.access.checker import payload
from buddy.brief.models import BriefItem

CASE = "brief_now"
STANDUP = ("стендап", "standup", "stand-up", "daily", "дейли")
ABSENCE = ("отпуск", "отсутств", "vacation", "out of office", "больнич", "day off")
DONE = ("done", "closed", "resolved", "готово", "закрыт")


@dataclass
class Collected:
    items: list[BriefItem] = field(default_factory=list)
    upcoming: list[dict] = field(default_factory=list)  # nearest meetings/deadlines: {title, url, at}
    calendar: dict | None = None  # current calendar snapshot, saved after a successful send
    events: list[dict] = field(default_factory=list)  # today's events for the schedule


def item_id(*parts: object) -> str:
    return "i-" + hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest()[:20]


def moment(value) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def login_of(value) -> str:
    if isinstance(value, dict):
        value = value.get("login") or value.get("name") or value.get("email") or ""
    return str(value or "").casefold()


def rows(value, *keys: str) -> list[dict]:
    if isinstance(value, dict):
        value = next((value[key] for key in keys if isinstance(value.get(key), list)), [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def call(runtime, context, server: str, tool: str, arguments: dict):
    return payload(await runtime.gateway.call(CASE, server, tool, arguments, user_id=context.user_id))


async def github(context, runtime, target: dict, login: str, since: datetime) -> Collected:
    result = Collected()
    owner, repo = target["owner"], target["repo"]
    listed = await call(
        runtime,
        context,
        "github",
        "list_pull_requests",
        {"owner": owner, "repo": repo, "state": "open", "perPage": 10},
    )
    mine = []
    for pull in rows(listed, "pull_requests", "items"):
        number, url = pull.get("number"), pull.get("html_url") or ""
        title = f"PR #{number} «{pull.get('title', '')}»"
        updated = moment(pull.get("updated_at"))
        if login_of(pull.get("user")) == login:
            mine.append((number, title, url))
        elif login in {login_of(item) for item in pull.get("requested_reviewers") or []}:
            if updated and updated > since:
                result.items.append(
                    BriefItem(
                        id=item_id(context.user_id, "gh", repo, number, "review"),
                        source="github",
                        type="pr_review_requested",
                        title=f"{title} — ждёт вашего ревью",
                        url=url,
                        priority_class="people",
                        related_task=task_key(pull.get("title", "")),
                        created_at=updated,
                    )
                )
    for number, title, url in mine[:3]:
        reviews = await call(
            runtime,
            context,
            "github",
            "pull_request_read",
            {"method": "get_reviews", "owner": owner, "repo": repo, "pullNumber": number},
        )
        latest: dict[str, dict] = {}
        for review in rows(reviews, "reviews", "items"):
            at = moment(review.get("submitted_at"))
            author = login_of(review.get("user"))
            if at and author and author != login and (author not in latest or at > latest[author]["at"]):
                latest[author] = {**review, "at": at}
        for author, review in latest.items():
            if review["at"] <= since:
                continue
            state = str(review.get("state", "")).upper()
            kind, priority = {
                "CHANGES_REQUESTED": ("pr_changes_requested", "blocker"),
                "APPROVED": ("pr_approved", "info"),
            }.get(state, ("pr_comment", "people"))
            verb = {"pr_changes_requested": "запрошены изменения", "pr_approved": "одобрен"}.get(
                kind, "новый комментарий"
            )
            result.items.append(
                BriefItem(
                    id=item_id(context.user_id, "gh", repo, number, review.get("id"), state),
                    source="github",
                    type=kind,
                    title=f"{title} — {verb} ({author})",
                    url=review.get("html_url") or url,
                    priority_class=priority,
                    related_task=task_key(title),
                    created_at=review["at"],
                )
            )
    return result


def task_key(text: str) -> str:
    found = re.search(r"\b[A-Z][A-Z0-9]{1,15}-\d{1,10}\b", text or "")
    return found.group() if found else ""


async def jira(context, runtime, target: dict, login: str, since: datetime, now: datetime) -> Collected:
    result = Collected()
    project = target["project_key"]
    found = await call(
        runtime,
        context,
        "jira",
        "jira_search",
        {
            "jql": f'project = {project} AND (assignee = "{login}" OR comment ~ "{login}") '
            "ORDER BY updated DESC",
            "fields": "summary,status,assignee,duedate,created,updated,comment",
            "limit": 20,
        },
    )
    for issue in rows(found, "issues", "items"):
        key, url = issue.get("key") or "", issue.get("url") or ""
        title = f"{key} «{issue.get('summary', '')}»"
        mine = login_of(issue.get("assignee")) == login
        status = issue.get("status")
        status = (status.get("name") if isinstance(status, dict) else status) or ""
        for comment in rows(issue.get("comments") or issue.get("comment"), "comments"):
            at = moment(comment.get("created"))
            author = login_of(comment.get("author"))
            if at and at > since and author != login and login in str(comment.get("body", "")).casefold():
                result.items.append(
                    BriefItem(
                        id=item_id(context.user_id, "jira", key, "comment", comment.get("id")),
                        source="jira",
                        type="jira_mention",
                        title=f"{title} — вас упомянули ({author})",
                        url=comment.get("url") or (f"{url}?focusedCommentId={comment['id']}" if url else ""),
                        priority_class="people",
                        related_task=key,
                        created_at=at,
                    )
                )
        created = moment(issue.get("created"))
        if mine and created and created > since:
            result.items.append(
                BriefItem(
                    id=item_id(context.user_id, "jira", key, "assigned"),
                    source="jira",
                    type="jira_assigned",
                    title=f"{title} — новая задача на вас",
                    url=url,
                    priority_class="people",
                    related_task=key,
                    created_at=created,
                )
            )
        due = issue.get("duedate")
        if mine and isinstance(due, str) and due and not any(word in status.casefold() for word in DONE):
            try:
                day = datetime.fromisoformat(due).date()
            except ValueError:
                continue
            left = (day - now.date()).days
            at = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo)
            if 0 <= left <= 7:
                result.upcoming.append({"title": f"срок {title}", "url": url, "at": at})
            if left in (0, 1) or left < 0:
                words = "срок сегодня" if left == 0 else "срок завтра" if left == 1 else "срок прошёл"
                result.items.append(
                    BriefItem(
                        # The day is part of the ID: the same deadline is reminded once per day.
                        id=item_id(context.user_id, "jira", key, "due", now.date()),
                        source="jira",
                        type="jira_due",
                        title=f"{title} — {words}",
                        url=url,
                        priority_class="deadline",
                        related_task=key,
                        after=day.strftime("%d.%m"),
                        created_at=now,
                    )
                )
    return result


def snapshot_of(events: list[dict]) -> dict:
    result = {}
    for event in events:
        start = moment(event.get("start"))
        if event.get("id") and start and str(event.get("status", "")).casefold() != "cancelled":
            result[str(event["id"])] = {
                "start": start.isoformat(),
                "summary": str(event.get("summary", "")),
                "url": event.get("htmlLink") or "",
            }
    return {"events": result}


def clock(value: datetime, other: datetime) -> str:
    return value.strftime("%H:%M") if value.date() == other.date() else value.strftime("%d.%m %H:%M")


async def calendar(context, runtime, target: dict, saved: dict | None, now: datetime) -> Collected:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    listed = await call(
        runtime,
        context,
        "calendar",
        "list-events",
        {
            "calendarId": target["calendarId"],
            "timeMin": start.isoformat(),
            "timeMax": (start + timedelta(days=1)).isoformat(),
        },
    )
    events = rows(listed, "events", "items")
    current = snapshot_of(events)
    result = Collected(calendar=current, events=events)
    for event in current["events"].values():
        at = datetime.fromisoformat(event["start"])
        if at >= now:
            result.upcoming.append({"title": event["summary"], "url": event["url"], "at": at})
    if saved is None:  # the first run only records the baseline (FR-H0-01: comparison of snapshots)
        return result
    before = saved.get("events", {})
    for key, event in current["events"].items():
        after_at = datetime.fromisoformat(event["start"])
        if key in before:
            before_at = datetime.fromisoformat(before[key]["start"])
            # A recurring meeting on another day is a new occurrence, not a move.
            if before_at != after_at and before_at.date() == after_at.date():
                result.items.append(
                    BriefItem(
                        id=item_id(context.user_id, "cal", key, "moved", event["start"]),
                        source="calendar",
                        type="meeting_moved",
                        title=f"«{event['summary']}» перенесена",
                        url=event["url"],
                        priority_class="deadline",
                        related_task=task_key(event["summary"]),
                        before=clock(before_at, after_at),
                        after=clock(after_at, before_at),
                        created_at=now,
                    )
                )
        elif any(datetime.fromisoformat(item["start"]).date() == now.date() for item in before.values()):
            result.items.append(
                BriefItem(
                    id=item_id(context.user_id, "cal", key, "new", event["start"]),
                    source="calendar",
                    type="meeting_new",
                    title=f"новая встреча «{event['summary']}»",
                    url=event["url"],
                    priority_class="deadline",
                    related_task=task_key(event["summary"]),
                    after=after_at.strftime("%H:%M"),
                    created_at=now,
                )
            )
    for key, event in before.items():
        at = datetime.fromisoformat(event["start"])
        if key not in current["events"] and at.date() == now.date() and at >= now:
            result.items.append(
                BriefItem(
                    id=item_id(context.user_id, "cal", key, "cancelled", event["start"]),
                    source="calendar",
                    type="meeting_cancelled",
                    title=f"«{event['summary']}» отменена",
                    url=event["url"],
                    priority_class="info",
                    related_task=task_key(event["summary"]),
                    before=at.strftime("%H:%M"),
                    created_at=now,
                )
            )
    return result
