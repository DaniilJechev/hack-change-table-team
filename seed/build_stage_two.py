"""Build and validate only synthetic stage-two data and the agent's immutable demo bundle."""

import json
from pathlib import Path

import yaml

from buddy.demo.contracts import AccessCatalog, TeamCard, validate_seed

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "Синтетические учебные данные; не регламент и не реальные данные Авито."


def build(root: Path = ROOT):
    scenario = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))
    users = yaml.safe_load((ROOT / "seed/users.yaml").read_text(encoding="utf-8"))["users"]
    if not any(user["user_id"] == "demo-petr" for user in users):
        users.append(
            {"user_id": "demo-petr", "display_name": "Пётр", "role": "mentor", "team_id": "demo-orders"}
        )

    def source(section):
        return "https://example.invalid/team/demo-orders/" + section

    card = {
        "synthetic": True,
        "team_id": "demo-orders",
        "name": "Учебная команда заказов",
        "notice": NOTICE,
        "source_url": source("card"),
        "people": [
            {
                "user_id": "demo-daniil",
                "name": "Даниил",
                "role": "Backend-стажёр",
                "areas": ["онбординг"],
                "contact_url": source("people/demo-daniil"),
            },
            {
                "user_id": "demo-anna",
                "name": "Анна",
                "role": "Наставник",
                "areas": ["онбординг", "ревью"],
                "contact_url": source("people/demo-anna"),
            },
            {
                "user_id": "demo-oleg",
                "name": "Олег",
                "role": "Тимлид",
                "areas": ["orders-api", "надёжность"],
                "first_contact": True,
                "contact_url": source("people/demo-oleg"),
            },
            {
                "user_id": "demo-petr",
                "name": "Пётр",
                "role": "Инженер интеграций",
                "areas": ["Kafka", "интеграции"],
                "contact_url": source("people/demo-petr"),
            },
        ],
        "routing": [
            {
                "situation": "упал staging",
                "go_to": "#orders-duty",
                "owner_id": "demo-oleg",
                "wait_minutes": 30,
                "then_id": "demo-oleg",
                "source_url": source("routing/staging"),
            },
            {
                "situation": "вопрос по Kafka",
                "go_to": "Пётр",
                "owner_id": "demo-petr",
                "wait_minutes": 60,
                "then_id": "demo-oleg",
                "source_url": source("routing/kafka"),
            },
        ],
        "do_not_disturb": [
            {
                "person_id": "demo-petr",
                "direct_allowed": False,
                "reason": "Вне учебного рабочего времени — через первый контакт",
                "via_id": "demo-oleg",
            }
        ],
        "tools": [
            "Go",
            "PostgreSQL",
            "Kafka",
            "Python",
            "GitHub",
            "Jira",
            "Miro",
            "Google Calendar",
            "Telegram",
        ],
        "links": {name: item["url"] for name, item in scenario["resources"].items()},
        "faq": [],
    }
    titles = {
        "github": "Репозиторий orders-onboarding",
        "jira": "Проект ORD",
        "miro": "Доска планирования",
        "google_calendar": "Командные встречи",
        "telegram": "Учебный командный чат",
    }
    ids = {
        "github": scenario["resources"]["github"]["repo"],
        "jira": "ORD",
        "miro": "demo-board-orders",
        "google_calendar": "demo-orders-calendar",
        "telegram": str(scenario["resources"]["telegram"]["chat_id"]),
    }
    objects = []
    for name in titles:
        server = "calendar" if name == "google_calendar" else name
        owner = "demo-anna" if name in {"google_calendar", "telegram"} else "demo-oleg"
        objects.append(
            {
                "object_id": "demo-orders-" + name,
                "instrument": name,
                "external_id": ids[name],
                "title": titles[name],
                "url": scenario["resources"][server]["url"],
                "request_url": source("requests/" + name),
                "owner_id": owner,
                "instructions": "Учебный сценарий: попросите владельца добавить вас; бот не меняет права.",
                "allowed": True,
            }
        )
    forbidden = {
        **objects[0],
        "object_id": "demo-orders-production",
        "external_id": "demo-buddy/production-config",
        "title": "Закрытая конфигурация production",
        "url": source("objects/production-config"),
        "allowed": False,
        "reason": "Этот учебный объект запрещён роли Backend-стажёр.",
    }
    catalog = {
        "synthetic": True,
        "notice": NOTICE,
        "team_id": "demo-orders",
        "role": "backend-intern",
        "instruments": list(titles),
        "objects": objects,
        "forbidden_objects": [forbidden],
    }
    validate_seed(TeamCard.model_validate(card), AccessCatalog.model_validate(catalog), users, scenario)
    outputs = {
        "seed/users.yaml": {"users": users},
        "seed/team_cards/demo-orders.yaml": card,
        "seed/access_catalog.yaml": catalog,
    }
    for name, value in outputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
        )
    data = root / "agent/buddy/demo/data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "scenario.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for step in scenario["steps"]:
        for original in sorted((ROOT / "fixtures" / step).rglob("*.json")):
            target = data / "fixtures" / original.relative_to(ROOT / "fixtures")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
    return card, catalog, users


if __name__ == "__main__":
    build()
    print("ST2-DATA-1: synthetic team card, five access objects, forbidden object and demo bundle validated")
