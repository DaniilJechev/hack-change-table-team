"""Offline counterparts of /api/v1/mock; only our simulation state is written."""

import re
import uuid
from copy import deepcopy

from buddy.data.contracts import DataError

EQUATOR_ITEM = "equator_presentation"
EQUATOR_TITLE = "Презентация к экватору"
ACCESS_STATUSES = {"granted", "pending", "denied", "error"}
ACCESS_ERRORS = {"service_unavailable", "service_unconfigured", "rate_limited"}


class MockServicesState:
    def _mock_init(self, rules):
        self.mock_rules = deepcopy(rules)
        self.mock_step = "step_1"
        self.mock_requests = {}
        self.mock_progress = {}

    def set_mock_step(self, step: str) -> None:
        self.mock_step = step
        for record in self.mock_requests.values():
            rule = self._mock_rule(record["team_id"], record["object_id"])
            outcome = rule.get("demo_steps", {}).get(step)
            if record["status"] == "pending" and outcome in {"granted", "denied"}:
                record.update(status=outcome, reason="Учебное согласование завершено", updated_at=self._now())

    def _mock_rule(self, team_id, object_id):
        return next(
            (
                rule
                for rule in self.mock_rules.get("access_rules", [])
                if rule["team_id"] == team_id and rule["object_id"] == object_id
            ),
            {
                "status": "error",
                "reason": "Правило имитации не настроено",
                "error_code": "service_unconfigured",
            },
        )

    async def request_access(self, user_id: str, object_id: str) -> dict:
        user = self._user(user_id)
        objects = {item["id"]: item for item in await self.access_catalog(user_id)}
        if object_id not in objects:
            raise DataError("not_found")
        if objects[object_id].get("forbidden"):
            raise DataError("forbidden")
        key = (user_id, object_id)
        existing = self.mock_requests.get(key)
        if existing and existing["status"] != "error":
            return deepcopy(existing)
        rule = self._mock_rule(user["team_id"], object_id)
        status = rule.get("demo_steps", {}).get(self.mock_step, rule["status"])
        if status not in ACCESS_STATUSES:
            raise DataError("invalid")
        now = self._now()
        record = {
            "id": existing["id"] if existing else "ar-" + uuid.uuid4().hex,
            "user_id": user_id,
            "team_id": user["team_id"],
            "object_id": object_id,
            "status": status,
            "reason": rule.get("reason", ""),
            "error_code": rule.get("error_code", ""),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "simulated": True,
        }
        if status == "granted":
            record["reason"] = "Учебный доступ выдан"
        self.mock_requests[key] = record
        if status in {"pending", "granted"}:
            await self.mark_requested(user_id, object_id)
        return deepcopy(record)

    async def access_requests(self, user_id: str) -> list[dict]:
        self._user(user_id)
        return [
            deepcopy(record) for (owner, _), record in sorted(self.mock_requests.items()) if owner == user_id
        ]

    async def resolve_access_request(
        self, user_id: str, request_id: str, status: str, reason: str = ""
    ) -> dict:
        user = self._user(user_id)
        if user["role"] != "teamlead":
            raise DataError("forbidden")
        if (
            status not in {"granted", "denied"}
            or (status == "denied" and not reason.strip())
            or len(reason) > 1000
        ):
            raise DataError("invalid")
        record = next(
            (
                item
                for item in self.mock_requests.values()
                if item["id"] == request_id and item["team_id"] == user["team_id"]
            ),
            None,
        )
        if record is None:
            raise DataError("not_found")
        if record["status"] != "pending":
            if record["status"] != status or record["reason"] != reason:
                raise DataError("conflict")
            return deepcopy(record)
        record.update(status=status, reason=reason, error_code="", updated_at=self._now())
        return deepcopy(record)

    async def progress(self, user_id: str) -> list[dict]:
        self._user(user_id)
        stored = self.mock_progress.get(user_id)
        return [
            deepcopy(stored)
            if stored
            else {
                "item_id": EQUATOR_ITEM,
                "title": EQUATOR_TITLE,
                "completed": False,
                "completed_at": None,
                "updated_at": None,
                "simulated": True,
            }
        ]

    async def mark_progress(self, user_id: str, item_id: str, completed: bool = True) -> dict:
        self._user(user_id)
        if not isinstance(completed, bool):
            raise DataError("invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item_id) or item_id != EQUATOR_ITEM:
            raise DataError("not_found")
        old = self.mock_progress.get(user_id)
        if old and old["completed"] == completed:
            return deepcopy(old)
        now = self._now()
        record = {
            "item_id": EQUATOR_ITEM,
            "title": EQUATOR_TITLE,
            "completed": completed,
            "completed_at": now if completed else None,
            "updated_at": now,
            "simulated": True,
        }
        self.mock_progress[user_id] = record
        return deepcopy(record)
