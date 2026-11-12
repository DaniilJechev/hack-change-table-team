"""In-memory data for the offline demo and tests; mirrors backend rights (stage-two backend-data spec)."""

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import yaml
from pydantic import ValidationError

from buddy.data.base import DataError
from buddy.data.briefs import BriefState
from buddy.data.mock_services import MockServicesState
from buddy.demo.contracts import TeamCard

BUNDLE = Path(__file__).resolve().parents[1] / "demo" / "data" / "seed.json"


def load_bundle(path: Path = BUNDLE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SeedData(BriefState, MockServicesState):
    def __init__(self, bundle: dict | None = None, *, clock: Callable[[], datetime] | None = None):
        bundle = deepcopy(bundle if bundle is not None else load_bundle())
        self.users = {user["user_id"]: user for user in bundle["users"]}
        self.card = bundle["card"]
        self.catalog = bundle["catalog"]
        self.targets = bundle.get("targets", {})
        self.clock = clock or (lambda: datetime.now(UTC))
        self.marks: dict[str, dict[str, str]] = {}
        self.snapshots: dict[str, dict] = {}
        self.questions: dict[str, dict] = {}
        self.faq: dict[str, dict] = {}
        self.card_version = 1
        self.team_flags: dict[str, dict[str, bool]] = {}
        mock_rules = bundle.get("mock_services")
        if mock_rules is None:
            mock_rules = json.loads(BUNDLE.with_name("mock_services.json").read_text())
        self._mock_init(mock_rules)
        # Yesterday's calendar of the synthetic day: the first demo brief compares against it.
        self._briefs()["calendar"].update(deepcopy(bundle.get("calendar_baseline", {})))

    def _user(self, user_id: str) -> dict:
        try:
            return self.users[user_id]
        except KeyError:
            raise DataError("unauthorized") from None

    def _now(self) -> str:
        return self.clock().isoformat()

    def target(self, object_id: str) -> dict | None:
        return self.targets.get(object_id)

    async def close(self) -> None:
        return None

    async def profile(self, user_id: str) -> dict:
        user = self._user(user_id)
        lead = next(
            (
                item["user_id"]
                for item in self.users.values()
                if item["team_id"] == user["team_id"] and item["role"] == "teamlead"
            ),
            None,
        )
        result = {
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "role": user["role"],
            "team_id": user["team_id"],
            "logins": dict(user.get("logins", {})),
        }
        if user.get("mentor_id"):
            result["mentor_id"] = user["mentor_id"]
        if lead:
            result["teamlead_id"] = lead
        return result

    async def team_card(self, user_id: str) -> dict:
        user = self._user(user_id)
        if user["team_id"] != self.card["team_id"]:
            raise DataError("not_found")
        return {
            "team_id": self.card["team_id"],
            "version": self.card_version,
            "document": deepcopy(self.card),
            "source_path": self.card["source_url"],
        }

    async def faq_search(self, user_id: str, text: str) -> list[dict]:
        user = self._user(user_id)
        needle = text.casefold().strip()
        if not needle:
            return []
        return [
            deepcopy(item)
            for item in self.faq.values()
            if item["team_id"] == user["team_id"]
            and (needle in item["question"].casefold() or needle in item["answer"].casefold())
        ][:20]

    async def access_catalog(self, user_id: str) -> list[dict]:
        user = self._user(user_id)
        if user["team_id"] != self.catalog["team_id"]:
            return []
        return [deepcopy(item) for item in self.catalog["objects"] if user["role"] in item["roles"]]

    async def access_marks(self, user_id: str) -> dict[str, str]:
        self._user(user_id)
        return dict(self.marks.get(user_id, {}))

    async def mark_requested(self, user_id: str, object_id: str) -> str:
        visible = {item["id"]: item for item in await self.access_catalog(user_id)}
        if object_id not in visible or visible[object_id]["forbidden"]:
            raise DataError("not_found")
        return self.marks.setdefault(user_id, {}).setdefault(object_id, self._now())

    async def interns(self, user_id: str) -> list[dict]:
        user = self._user(user_id)
        if user["role"] == "intern":
            raise DataError("forbidden")
        result = []
        for item in self.users.values():
            if item["role"] != "intern" or item["team_id"] != user["team_id"]:
                continue
            if user["role"] == "mentor" and item.get("mentor_id") != user["user_id"]:
                continue
            result.append(
                {
                    "user_id": item["user_id"],
                    "display_name": item["display_name"],
                    "role": "intern",
                    "team_id": item["team_id"],
                    "snapshot": deepcopy(self.snapshots.get(item["user_id"])),
                }
            )
        return result

    async def save_snapshot(self, user_id: str, snapshot_id: str, objects: list[dict]) -> None:
        self._user(user_id)
        self.snapshots[user_id] = {"id": snapshot_id, "objects": deepcopy(objects), "created_at": self._now()}

    async def create_question(self, user_id: str, question_id: str, text: str, assigned_to: str) -> dict:
        user = self._user(user_id)
        if assigned_to and not (await self.flags(user_id))["team_escalation"]:
            raise DataError("forbidden")
        target = self.users.get(assigned_to)
        if (assigned_to and (target is None or target["team_id"] != user["team_id"])) or not text.strip():
            raise DataError("invalid")
        record = self.questions.setdefault(
            question_id,
            {
                "id": question_id,
                "user_id": user_id,
                "team_id": user["team_id"],
                "text": text.strip(),
                "assigned_to": assigned_to,
                "answer": "",
                "helpful": None,
                "created_at": self._now(),
            },
        )
        if (record["user_id"], record["text"], record["assigned_to"]) != (user_id, text.strip(), assigned_to):
            raise DataError("conflict")
        return deepcopy(record)

    def _question_for(self, user_id: str, question_id: str) -> dict:
        self._user(user_id)
        record = self.questions.get(question_id)
        if record is None or user_id not in (record["user_id"], record["assigned_to"]):
            raise DataError("not_found")
        return record

    async def question(self, user_id: str, question_id: str) -> dict:
        return deepcopy(self._question_for(user_id, question_id))

    async def answer_question(self, user_id: str, question_id: str, text: str) -> dict:
        record = self._question_for(user_id, question_id)
        if record["assigned_to"] != user_id:
            raise DataError("forbidden")
        if not text.strip():
            raise DataError("invalid")
        if record["answer"] and record["answer"] != text.strip():
            raise DataError("conflict")
        record["answer"] = text.strip()
        return deepcopy(record)

    async def rate_question(self, user_id: str, question_id: str, helpful: bool) -> None:
        record = self._question_for(user_id, question_id)
        if record["user_id"] != user_id or not record["answer"]:
            raise DataError("forbidden")
        record["helpful"] = helpful

    async def publish_faq(self, user_id: str, faq_id: str, question_id: str) -> dict:
        if not (await self.flags(user_id))["faq_publication"]:
            raise DataError("forbidden")
        record = self._question_for(user_id, question_id)
        if record["assigned_to"] != user_id or not record["answer"]:
            raise DataError("forbidden")
        entry = self.faq.setdefault(
            faq_id,
            {
                "id": faq_id,
                "question_id": question_id,
                "team_id": record["team_id"],
                "question": record["text"],
                "answer": record["answer"],
                "author_id": user_id,
                "created_at": self._now(),
                "source_path": "https://example.invalid/team/"
                + quote(record["team_id"], safe="")
                + "/faq/"
                + quote(faq_id, safe=""),
            },
        )
        return deepcopy(entry)

    async def record_bot_answer(
        self,
        user_id: str,
        question_id: str,
        text: str,
        answer: str,
        sources: list[str],
        capability: str = "h2",
    ) -> dict:
        if not answer.strip() or not sources:
            raise DataError("invalid")
        await self.create_question(user_id, question_id, text, "")
        record = self.questions[question_id]
        if record.get("bot_answer") and (record["bot_answer"], record["bot_sources"]) != (answer, sources):
            raise DataError("conflict")
        record.update(bot_answer=answer, bot_sources=list(sources), capability=capability)
        return deepcopy(record)

    async def rate_bot_answer(self, user_id: str, question_id: str, helpful: bool) -> None:
        record = self._question_for(user_id, question_id)
        if record["user_id"] != user_id or not record.get("bot_answer"):
            raise DataError("forbidden")
        record["bot_helpful"] = helpful

    async def flags(self, user_id: str) -> dict[str, bool]:
        user = self._user(user_id)
        return {
            "access_monitoring": True,
            "team_escalation": True,
            "faq_publication": True,
            **self.team_flags.get(user["team_id"], {}),
        }

    async def upload_team_card(self, user_id: str, source: bytes) -> dict:
        user = self._user(user_id)
        if user["role"] not in {"mentor", "teamlead"}:
            raise DataError("forbidden")
        if len(source) > 1 << 20:
            raise DataError("invalid", fields=("document: файл больше 1 МиБ",))
        try:
            document = yaml.safe_load(source)
            validated = TeamCard.model_validate(document)
        except (yaml.YAMLError, UnicodeError):
            raise DataError("invalid", fields=("document: ожидается корректный YAML",)) from None
        except ValidationError as error:
            paths = []
            for item in error.errors(include_input=False):
                parts = list(item["loc"])
                if item["type"] == "extra_forbidden":
                    parts[-1] = "*"
                field = "".join(
                    f"[{part}]" if isinstance(part, int) else ("." if i else "") + str(part)
                    for i, part in enumerate(parts)
                )
                paths.append(
                    field + (": обязательное поле" if item["type"] == "missing" else ": неверное значение")
                )
            raise DataError("invalid", fields=tuple(paths[:20])) from None
        if validated.team_id != user["team_id"]:
            raise DataError("forbidden")
        team_users = {uid for uid, value in self.users.items() if value["team_id"] == user["team_id"]}
        if not {person.user_id for person in validated.people} <= team_users:
            raise DataError("invalid", fields=("people: участник своей команды не найден",))
        if document != self.card:
            self.card = deepcopy(document)
            self.card_version += 1
        return await self.team_card(user_id)

    async def latest_snapshot(self, user_id: str) -> dict | None:
        self._user(user_id)
        return deepcopy(self.snapshots.get(user_id))

    async def save_brief(self, user_id: str, brief_id: str, items: list[dict]) -> None:
        self._user(user_id)
        self.brief_create(user_id, brief_id, items, self._now())

    async def brief_status(
        self, user_id: str, brief_id: str, status: str, calendar: dict | None = None, at: str = ""
    ) -> None:
        self._user(user_id)
        self.brief_mark(user_id, brief_id, status, at or self._now(), calendar)

    async def brief_event(self, user_id: str, brief_id: str, kind: str, item_id: str = "") -> None:
        self._user(user_id)
        self.brief_signal(user_id, brief_id, kind, self._now(), item_id)

    async def brief_stats(self, user_id: str) -> list[dict]:
        user = self._user(user_id)
        if user["role"] == "intern":
            people = [user]
        else:
            people = [self.users[item["user_id"]] for item in await self.interns(user_id)]
        return [
            {
                "user_id": item["user_id"],
                "display_name": item["display_name"],
                **self.brief_counts(item["user_id"]),
            }
            for item in people
        ]
