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
        },
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
                        "title": "Учебный ORD-118",
                        "html_url": resources["github"]["url"],
                        "requested_reviewers": ["demo-anna"],
                    }
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
                    }
                ]
            },
            "miro": {
                "contentItemId": "demo-board-orders",
                "roles": [
                    {"user_id": "demo-anna", "role": "viewer"},
                    *([{"user_id": "demo-daniil", "role": "viewer"}] if step == "step_2" else []),
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
