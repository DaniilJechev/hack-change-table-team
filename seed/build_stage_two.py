"""Build and validate only synthetic stage-two data and the agent's immutable demo bundle."""

import json
from pathlib import Path

import yaml

from buddy.demo.contracts import AccessCatalog, TeamCard, access_urls, validate_seed

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "Синтетические учебные данные; не регламент и не реальные данные Авито."
LOGINS = {
    "demo-daniil": {
        "github": "demo-daniil",
        "jira": "demo.daniil",
        "miro": "demo-daniil",
        "google": "daniil@example.invalid",
        "telegram": "10001",
    },
    "demo-anna": {
        "github": "demo-anna",
        "jira": "demo.anna",
        "miro": "demo-anna",
        "google": "anna@example.invalid",
    },
    "demo-oleg": {
        "github": "demo-oleg",
        "jira": "demo.oleg",
        "miro": "demo-oleg",
        "google": "oleg@example.invalid",
    },
    "demo-petr": {"github": "demo-petr", "jira": "demo.petr", "google": "petr@example.invalid"},
    "demo-kirill": {"github": "demo-kirill", "jira": "demo.kirill", "google": "kirill@example.invalid"},
}


def build(root: Path = ROOT):
    scenario = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))
    users = yaml.safe_load((ROOT / "seed/users.yaml").read_text(encoding="utf-8"))["users"]
    if not any(user["user_id"] == "demo-petr" for user in users):
        users.append(
            {"user_id": "demo-petr", "display_name": "Пётр", "role": "mentor", "team_id": "demo-orders"}
        )
    for user in users:
        if user["user_id"] in LOGINS:
            user["logins"] = LOGINS[user["user_id"]]

    def source(section):
        return "https://example.invalid/team/demo-orders/" + section

    urls = access_urls(scenario)
    card = {
        "synthetic": True,
        "team_id": "demo-orders",
        "name": "Учебная команда заказов",
        "notice": NOTICE,
        "source_url": source("card"),
        "stage": {"epic_key": "ORD-100", "source_url": scenario["resources"]["jira"]["epic_url"]},
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
        "links": [
            {"service": ("calendar" if name == "google_calendar" else name), "url": urls[name]}
            for name in ("github", "jira", "miro", "google_calendar", "telegram")
        ],
        "faq": [],
    }
    titles = {
        "github": "Репозиторий orders-onboarding",
        "jira": "Проект ORD",
        "miro": "Доска планирования",
        "google_calendar": "Командные встречи",
        "telegram": "Учебный командный чат",
    }
    objects = []
    for name in titles:
        owner = "demo-anna" if name in {"google_calendar", "telegram"} else "demo-oleg"
        objects.append(
            {
                "id": "demo-orders-" + name,
                "instrument": name,
                "name": titles[name],
                "url": urls[name],
                "request_url": source("requests/" + name),
                "owner_id": owner,
                "roles": ["intern", "mentor", "teamlead"],
                "forbidden": False,
            }
        )
    objects.append(
        {
            "id": "demo-orders-production",
            "instrument": "github",
            "name": "Закрытая конфигурация production",
            "url": source("objects/production-config"),
            "owner_id": "demo-oleg",
            "roles": ["intern"],
            "forbidden": True,
            "reason": "Этот учебный объект запрещён роли Backend-стажёр.",
        }
    )
    # Format matches backend/internal/store CatalogObject (strict KnownFields parser).
    catalog = {
        "synthetic": True,
        "notice": NOTICE,
        "team_id": "demo-orders",
        "instruments": list(titles),
        "objects": objects,
    }
    validate_seed(TeamCard.model_validate(card), AccessCatalog.model_validate(catalog), users, scenario)
    resources = scenario["resources"]
    owner, repo = resources["github"]["repo"].split("/")
    targets = {
        "demo-orders-github": {
            "owner": owner,
            "repo": repo,
            "keywords": ["orders-onboarding", "репозитор", "github"],
        },
        "demo-orders-jira": {"project_key": resources["jira"]["project"], "keywords": ["jira", "ord-"]},
        "demo-orders-miro": {"board_id": resources["miro"]["board_id"], "keywords": ["miro", "доск"]},
        "demo-orders-google_calendar": {
            **{key: value for key, value in scenario["calls"]["calendar"]["arguments"].items()},
            "keywords": ["стендап", "встреч", "календар"],
        },
        "demo-orders-telegram": {
            "chat_id": resources["telegram"]["chat_id"],
            "keywords": ["чат", "telegram"],
        },
        "demo-orders-production": {"keywords": ["production"]},
    }
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
    # Runtime demo bundle: JSON only (PyYAML is a dev dependency of the agent).
    bundle = {"users": users, "card": card, "catalog": catalog, "targets": targets}
    (data / "seed.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
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
