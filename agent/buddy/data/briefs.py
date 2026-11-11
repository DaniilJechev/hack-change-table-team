"""In-process state of morning briefs (H0): drafts, delivery, read signals, calendar snapshot, settings.

SeedData uses it as the whole storage of the offline demo. BackendData keeps the same state next to
the REST calls, because the stage-two backend stores briefs and events but has no endpoints for the
last sent time, calendar snapshots, statistics or settings yet (stage 4, backend part).
"""

from copy import deepcopy

from buddy.data.base import DataError

READ_KINDS = {"read", "click", "positive", "negative", "reply"}
EVENT_KINDS = READ_KINDS


class BriefState:
    def _briefs(self) -> dict:
        if not hasattr(self, "_brief_state"):
            self._brief_state = {"briefs": {}, "calendar": {}, "settings": {}}
        return self._brief_state

    def _brief(self, user_id: str, brief_id: str) -> dict:
        record = self._briefs()["briefs"].get(brief_id)
        if record is None or record["user_id"] != user_id:
            raise DataError("not_found")
        return record

    def brief_create(self, user_id: str, brief_id: str, items: list[dict], created_at: str) -> None:
        briefs = self._briefs()["briefs"]
        if brief_id in briefs:
            raise DataError("conflict")
        briefs[brief_id] = {
            "id": brief_id,
            "user_id": user_id,
            "items": deepcopy(items),
            "status": "draft",
            "created_at": created_at,
            "sent_at": None,
            "read_at": None,
            "events": [],
        }

    def brief_mark(
        self, user_id: str, brief_id: str, status: str, at: str, calendar: dict | None = None
    ) -> None:
        record = self._brief(user_id, brief_id)
        if status not in {"sent", "failed"}:
            raise DataError("invalid")
        if record["status"] == "sent":  # sent is never downgraded
            return
        record["status"] = status
        if status == "sent":
            record["sent_at"] = at
            if calendar is not None:
                self._briefs()["calendar"][user_id] = deepcopy(calendar)

    def brief_signal(self, user_id: str, brief_id: str, kind: str, at: str, item_id: str = "") -> None:
        record = self._brief(user_id, brief_id)
        if kind not in EVENT_KINDS or record["status"] != "sent":
            raise DataError("invalid")
        if kind == "click" and item_id not in {item["id"] for item in record["items"]}:
            raise DataError("invalid")
        record["events"].append({"kind": kind, "item_id": item_id, "at": at})
        record["read_at"] = record["read_at"] or at  # the first signal marks the brief as read

    async def brief(self, user_id: str, brief_id: str) -> dict:
        return deepcopy(self._brief(user_id, brief_id))

    async def last_brief_sent(self, user_id: str) -> str | None:
        sent = [
            record["sent_at"]
            for record in self._briefs()["briefs"].values()
            if record["user_id"] == user_id and record["status"] == "sent"
        ]
        return max(sent) if sent else None

    async def brief_seen(self, user_id: str) -> set[str]:
        """Item IDs already delivered to the user: the same change is never reported twice."""
        return {
            item["id"]
            for record in self._briefs()["briefs"].values()
            if record["user_id"] == user_id and record["status"] == "sent"
            for item in record["items"]
        }

    async def calendar_snapshot(self, user_id: str) -> dict | None:
        return deepcopy(self._briefs()["calendar"].get(user_id))

    def brief_counts(self, user_id: str) -> dict[str, int]:
        sent = [
            record
            for record in self._briefs()["briefs"].values()
            if record["user_id"] == user_id and record["status"] == "sent"
        ]
        return {
            "sent": len(sent),
            "read": sum(record["read_at"] is not None for record in sent),
            "clicked": sum(any(e["kind"] == "click" for e in record["events"]) for record in sent),
            "positive": sum(any(e["kind"] == "positive" for e in record["events"]) for record in sent),
            "negative": sum(any(e["kind"] == "negative" for e in record["events"]) for record in sent),
        }

    async def brief_settings(self, user_id: str) -> dict:
        return {"time": None, "quiet": False, **self._briefs()["settings"].get(user_id, {})}

    async def set_brief_settings(self, user_id: str, **changes) -> dict:
        allowed = {key: value for key, value in changes.items() if key in {"time", "quiet"}}
        self._briefs()["settings"].setdefault(user_id, {}).update(allowed)
        return await self.brief_settings(user_id)
