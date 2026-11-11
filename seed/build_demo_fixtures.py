"""Build only synthetic stage-1 data. No external service calls."""

import json
from pathlib import Path

from buddy.mcp_hub.fixtures import FixtureStore

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = "2026-11-10T12:00:00+00:00"
# Stage-3 checks reuse official runtime discovery schemas; results stay synthetic.
OFFICIAL = {
    ("github", "list_repository_collaborators"): "mcp/discovery/github.json",
    ("jira", "jira_search_assignable_users"): "mcp/discovery/jira.json",
    ("github", "pull_request_read"): "mcp/discovery/github.json",
    ("jira", "jira_search"): "mcp/discovery/jira.json",
}
ZONE = "+03:00"
# The synthetic day: each demo step has a fixed "now" (FR-H0: reproducible calendar window).
CLOCK = {
    "step_1": "2026-11-11T09:00:00" + ZONE,
    "step_2": "2026-11-11T09:00:00" + ZONE,
    "step_3": "2026-11-12T09:00:00" + ZONE,
}
BRIEF_JQL = 'project = ORD AND (assignee = "demo.daniil" OR comment ~ "demo.daniil") ORDER BY updated DESC'


def brief_fixtures(step: str, resources: dict) -> dict[str, list]:
    """H0 fixtures of a step: (official tool or None, arguments, payload) per server."""
    base = "https://example.invalid"
    repo = base + "/github/demo-buddy/orders-onboarding"
    next_day = step == "step_3"
    reviews = [
        {
            "id": 9001,
            "user": {"login": "demo-anna"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-11-10T18:20:00" + ZONE,
            "html_url": repo + "/pull/45#pullrequestreview-9001",
            "body": "Учебное замечание: добавить проверку пустого статуса.",
        }
    ]
    if next_day:
        reviews.append(
            {
                "id": 9002,
                "user": {"login": "demo-anna"},
                "state": "APPROVED",
                "submitted_at": "2026-11-11T16:00:00" + ZONE,
                "html_url": repo + "/pull/45#pullrequestreview-9002",
                "body": "Учебное одобрение.",
            }
        )
    comments = [
        {
            "id": "5001",
            "author": {"name": "demo.oleg", "display_name": "Олег (демо)"},
            "body": "demo.daniil, сверь, пожалуйста, фильтр с планом на доске.",
            "created": "2026-11-10T19:05:00" + ZONE,
            "url": base + "/jira/browse/ORD-118?focusedCommentId=5001",
        }
    ]
    if next_day:
        comments.append(
            {
                "id": "5002",
                "author": {"name": "demo.petr", "display_name": "Пётр (демо)"},
                "body": "demo.daniil, событие в Kafka для фильтра уже есть, можно использовать.",
                "created": "2026-11-11T15:00:00" + ZONE,
                "url": base + "/jira/browse/ORD-118?focusedCommentId=5002",
            }
        )
    issues = [
        {
            "key": "ORD-118",
            "summary": "Учебная проверка заказов",
            "status": {"name": "In Progress"},
            "assignee": {"name": "demo.daniil"},
            "duedate": "2026-11-12",
            "created": "2026-11-05T10:00:00" + ZONE,
            "updated": comments[-1]["created"],
            "url": resources["jira"]["url"],
            "comments": comments,
        },
        {
            "key": "ORD-121",
            "summary": "Учебные тесты фильтра",
            "status": {"name": "To Do"},
            "assignee": {"name": "demo.daniil"},
            "duedate": "2026-11-20",
            "created": "2026-11-10T16:00:00" + ZONE,
            "updated": "2026-11-10T16:00:00" + ZONE,
            "url": base + "/jira/browse/ORD-121",
            "comments": [],
        },
    ]
    day = "2026-11-12" if next_day else "2026-11-11"
    events = [
        {
            "id": "demo-standup",
            "summary": "Учебный стендап",
            "start": f"{day}T10:30:00{ZONE}",
            "htmlLink": resources["calendar"]["url"],
            "attendees": [{"email": "daniil@example.invalid", "responseStatus": "needsAction"}],
        }
    ]
    if next_day:
        events.append(
            {
                "id": "demo-team-demo",
                "summary": "Учебное демо команды",
                "start": f"{day}T16:00:00{ZONE}",
                "htmlLink": base + "/calendar/event/demo-team-demo",
            }
        )
    else:
        events.append(
            {
                "id": "demo-sync-ord-101",
                "summary": "Синк по ORD-101",
                "start": f"{day}T12:00:00{ZONE}",
                "htmlLink": base + "/calendar/event/demo-sync-ord-101",
            }
        )
    result = {
        "github": [
            (
                official_tool("github", "pull_request_read"),
                {
                    "method": "get_reviews",
                    "owner": "demo-buddy",
                    "repo": "orders-onboarding",
                    "pullNumber": 45,
                },
                {"reviews": reviews},
            )
        ],
        "jira": [
            (
                official_tool("jira", "jira_search"),
                {
                    "jql": BRIEF_JQL,
                    "fields": "summary,status,assignee,duedate,created,updated,comment",
                    "limit": 20,
                },
                {"issues": issues},
            )
        ],
    }
    if next_day:
        result["calendar"] = [
            (
                None,
                {
                    "calendarId": "demo-orders-calendar",
                    "timeMin": f"{day}T00:00:00{ZONE}",
                    "timeMax": f"2026-11-13T00:00:00{ZONE}",
                },
                {"events": events},
            )
        ]
    return result


# Yesterday evening's calendar of the synthetic day; the first brief is compared against it.
CALENDAR_BASELINE = {
    "demo-daniil": {
        "events": {
            "demo-standup": {
                "start": "2026-11-11T09:30:00" + ZONE,
                "summary": "Учебный стендап",
                "url": "https://example.invalid/calendar/event/demo-standup",
            },
            "demo-sync-ord-101": {
                "start": "2026-11-11T15:00:00" + ZONE,
                "summary": "Синк по ORD-101",
                "url": "https://example.invalid/calendar/event/demo-sync-ord-101",
            },
            "demo-retro": {
                "start": "2026-11-11T17:00:00" + ZONE,
                "summary": "Учебное ретро",
                "url": "https://example.invalid/calendar/event/demo-retro",
            },
        }
    }
}


def official_tool(server: str, name: str) -> dict:
    catalog = json.loads((ROOT / OFFICIAL[(server, name)]).read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["name"] == name)


NOTICE = "Синтетические данные учебного проекта; не регламент и не реальные данные Авито."


def build(root: Path = ROOT) -> None:
    examples = {
        "github": {
            "tool": "list_pull_requests",
            "arguments": {"owner": "demo-buddy", "repo": "orders-onboarding", "state": "open", "perPage": 10},
            "schema": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string"},
                "perPage": {"type": "integer", "minimum": 1},
            },
            "required": ["owner", "repo"],
        },
        "jira": {
            "tool": "jira_get_issue",
            "arguments": {"issue_key": "ORD-118"},
            "schema": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
        },
        "calendar": {
            "tool": "list-events",
            "arguments": {
                "calendarId": "demo-orders-calendar",
                "timeMin": "2026-11-11T00:00:00+03:00",
                "timeMax": "2026-11-12T00:00:00+03:00",
            },
            "schema": {key: {"type": "string"} for key in ["calendarId", "timeMin", "timeMax"]},
            "required": ["calendarId", "timeMin", "timeMax"],
        },
        "miro": {
            "tool": "content_item_list_roles",
            "arguments": {"contentItemId": "demo-board-orders"},
            "schema": {"contentItemId": {"type": "string"}},
            "required": ["contentItemId"],
        },
    }
    resources = {
        "github": {
            "repo": "demo-buddy/orders-onboarding",
            "pull_number": 45,
            "url": "https://example.invalid/github/demo-buddy/orders-onboarding/pull/45",
        },
        "jira": {
            "project": "ORD",
            "epic_key": "ORD-100",
            "issue_key": "ORD-118",
            "url": "https://example.invalid/jira/browse/ORD-118",
            "epic_url": "https://example.invalid/jira/browse/ORD-100",
        },
        "calendar": {
            "calendar_id": "demo-orders-calendar",
            "event_id": "demo-standup",
            "url": "https://example.invalid/calendar/event/demo-standup",
        },
        "miro": {
            "board_id": "demo-board-orders",
            "url": "https://example.invalid/miro/board/demo-board-orders",
        },
        "telegram": {"chat_id": -1001234567890, "url": "https://example.invalid/telegram/demo-orders"},
    }
    for resource in resources.values():
        resource["live_status"] = "not_created"
    scenario = {
        "version": 1,
        "synthetic": True,
        "notice": NOTICE,
        "team_id": "demo-orders",
        "user_ids": ["demo-daniil", "demo-anna", "demo-oleg"],
        "resources": resources,
        "calls": {
            server: {"tool": item["tool"], "arguments": item["arguments"]}
            for server, item in examples.items()
        },
        "steps": {
            "step_1": "PR требует внимания; задача назначена; встреча перенесена; Miro ещё нет",
            "step_2": "Появился доступ viewer к Miro",
            "step_3": "Следующее утро: PR одобрен, срок ORD-118 сегодня, новый комментарий в Jira",
        },
        "clock": CLOCK,
        "calendar_baseline": CALENDAR_BASELINE,
    }
    seed = root / "seed/day_scenario.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for step in scenario["steps"]:
        store = FixtureStore(root / "fixtures", step=step)
        payloads = {
            "github": {
                "pull_requests": [
                    {
                        "number": 45,
                        "state": "open",
                        "title": "ORD-118: фильтр по статусу",
                        "html_url": resources["github"]["url"],
                        "user": {"login": "demo-daniil"},
                        "requested_reviewers": [{"login": "demo-anna"}],
                        "updated_at": "2026-11-10T18:20:00" + ZONE,
                    },
                    {
                        "number": 47,
                        "state": "open",
                        "title": "ORD-119: учебный клиент Kafka",
                        "html_url": "https://example.invalid/github/demo-buddy/orders-onboarding/pull/47",
                        "user": {"login": "demo-petr"},
                        "requested_reviewers": [{"login": "demo-daniil"}],
                        "updated_at": "2026-11-10T17:00:00" + ZONE,
                    },
                ]
            },
            "jira": {
                "key": "ORD-118",
                "epic_key": "ORD-100",
                "assignee": "demo-daniil",
                "team_id": "demo-orders",
                "summary": "Учебная проверка заказов",
                "description": (
                    "Добавить фильтр по статусу в репозитории orders-onboarding, сверить план на доске "
                    "планирования в Miro и обсудить на стендапе. Конфигурацию production-config не менять."
                ),
                "url": resources["jira"]["url"],
            },
            "calendar": {
                "events": [
                    {
                        "id": "demo-standup",
                        "summary": "Учебный стендап",
                        "start": "2026-11-11T10:30:00+03:00",
                        "previous_start": "2026-11-11T09:30:00+03:00",
                        "htmlLink": resources["calendar"]["url"],
                        "task": "ORD-118",
                        "attendees": [
                            {"email": "anna@example.invalid", "responseStatus": "accepted"},
                            {"email": "oleg@example.invalid", "responseStatus": "accepted"},
                            {"email": "daniil@example.invalid", "responseStatus": "needsAction"},
                        ],
                    },
                    {
                        "id": "demo-sync-ord-101",
                        "summary": "Синк по ORD-101",
                        "start": "2026-11-11T12:00:00+03:00",
                        "htmlLink": "https://example.invalid/calendar/event/demo-sync-ord-101",
                    },
                ]
            },
            "miro": {
                "contentItemId": "demo-board-orders",
                "roles": [
                    {"user_id": "demo-anna", "role": "viewer"},
                    *([{"user_id": "demo-daniil", "role": "viewer"}] if step != "step_1" else []),
                ],
            },
        }
        extras = {
            "github": [
                (
                    official_tool("github", "list_repository_collaborators"),
                    {"owner": "demo-buddy", "repo": "orders-onboarding"},
                    {
                        "collaborators": [
                            {"login": "demo-anna", "role_name": "maintain"},
                            {"login": "demo-oleg", "role_name": "admin"},
                            {"login": "demo-daniil", "role_name": "write"},
                        ]
                    },
                )
            ],
            "jira": [
                (
                    official_tool("jira", "jira_search_assignable_users"),
                    {"query": "demo.daniil", "project_key": "ORD"},
                    {"users": [{"name": "demo.daniil", "displayName": "Даниил (демо)", "active": True}]},
                ),
                (
                    None,
                    {"issue_key": "ORD-100"},
                    {
                        "key": "ORD-100",
                        "issue_type": "Epic",
                        "summary": "Фильтры в новом API",
                        "team_id": "demo-orders",
                        "progress": {"done": 6, "total": 11},
                        "url": resources["jira"]["epic_url"],
                    },
                ),
            ],
        }
        for server, items in brief_fixtures(step, resources).items():
            extras.setdefault(server, []).extend(items)
        for server, example in examples.items():
            tool = {
                "name": example["tool"],
                "description": "Synthetic test contract, not live discovery",
                "inputSchema": {
                    "type": "object",
                    "properties": example["schema"],
                    "required": example["required"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True},
            }
            extra_tools = [item[0] for item in extras.get(server, []) if item[0] is not None]
            store.save_catalog(server, [tool, *extra_tools], origin="synthetic")
            result = {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"synthetic": True, "notice": NOTICE, **payloads[server]}, ensure_ascii=False
                        ),
                    }
                ],
            }
            store.save(server, example["tool"], example["arguments"], result, origin="synthetic")
            for definition, arguments, payload in extras.get(server, []):
                name = example["tool"] if definition is None else definition["name"]
                text = json.dumps({"synthetic": True, "notice": NOTICE, **payload}, ensure_ascii=False)
                store.save(
                    server,
                    name,
                    arguments,
                    {"isError": False, "content": [{"type": "text", "text": text}]},
                    origin="synthetic",
                )
        telegram = root / "fixtures" / step / "telegram"
        telegram.mkdir(parents=True, exist_ok=True)
        (telegram / "getChatMember.json").write_text(
            json.dumps(
                {
                    "origin": "synthetic",
                    "not_live": True,
                    "method": "getChatMember",
                    "arguments": {"chat_id": resources["telegram"]["chat_id"], "user_id": 10001},
                    "result": {
                        "status": "member",
                        "user": {"id": 10001, "first_name": "Демо", "is_bot": False},
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        # Synthetic generation is deterministic; real recording uses actual UTC capture time.
        for file in (root / "fixtures" / step).glob("*/*.json"):
            data = json.loads(file.read_text(encoding="utf-8"))
            if "captured_at" in data:
                data["captured_at"] = FIXED_TIME
            file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
            )


if __name__ == "__main__":
    build()
