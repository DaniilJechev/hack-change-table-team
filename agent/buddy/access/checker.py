"""H1: read-only access checks inside the tools themselves (FR-H1-03). No write tool is reachable."""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from buddy.mcp_hub.errors import MCPError, ToolDenied
from buddy.mcp_hub.policy import permitted

STALE_AFTER = timedelta(hours=48)
LOGIN_KEYS = {
    "github": "github",
    "jira": "jira",
    "miro": "miro",
    "google_calendar": "google",
    "telegram": "telegram",
}
TELEGRAM_MEMBER = {"member", "administrator", "creator"}
STATUSES = ("granted", "missing", "requested", "stale", "forbidden", "unknown")


@dataclass(frozen=True)
class AccessItem:
    object_id: str
    instrument: str
    name: str
    url: str
    request_url: str
    owner_id: str
    status: str
    reason: str = ""
    requested_at: str = ""
    error: str = ""


class Gateway(Protocol):
    async def call(self, case: str, server: str, tool: str, arguments: dict, *, user_id: str) -> dict: ...


class TelegramMembers(Protocol):
    async def status(self, chat_id: int, user_id: int) -> str: ...


class TelegramUnavailable(Exception):
    pass


class FixtureTelegram:
    """Synthetic getChatMember replay for the demo step; never a live call."""

    def __init__(self, root: Path, step: str):
        self.path = root / step / "telegram" / "getChatMember.json"

    async def status(self, chat_id: int, user_id: int) -> str:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise TelegramUnavailable() from None
        if value.get("origin") != "synthetic" or value.get("arguments") != {
            "chat_id": chat_id,
            "user_id": user_id,
        }:
            raise TelegramUnavailable()
        return str(value.get("result", {}).get("status", ""))


class BotTelegram:
    """Live getChatMember through the bot (bot must be an administrator of the team chat)."""

    def __init__(self, bot):
        self.bot = bot

    async def status(self, chat_id: int, user_id: int) -> str:  # pragma: no cover - live Telegram only
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except Exception:
            raise TelegramUnavailable() from None
        return str(member.status.value if hasattr(member.status, "value") else member.status)


def payload(result: dict):
    """Decode JSON carried in MCP TextContent; structuredContent wins when present."""
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    values = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                values.append(json.loads(item["text"]))
            except (TypeError, ValueError):
                values.append(item.get("text"))
    return values[0] if len(values) == 1 else values


def contains(value, login: str) -> bool:
    needle = login.casefold()
    if isinstance(value, str):
        return value.casefold() == needle
    if isinstance(value, dict):
        return any(contains(child, login) for child in value.values())
    if isinstance(value, list):
        return any(contains(child, login) for child in value)
    return False


def member_in_response(instrument: str, value, login: str) -> bool | None:
    """Match only participant identity fields; unrelated text is not membership evidence."""
    keys = {"github": "collaborators", "jira": "users", "miro": "roles", "google_calendar": "events"}
    rows = (
        value if isinstance(value, list) else value.get(keys[instrument]) if isinstance(value, dict) else None
    )
    if not isinstance(rows, list):
        return None
    needle = login.casefold()
    for row in rows:
        if not isinstance(row, dict):
            return None
        if instrument == "google_calendar":
            if row.get("status") == "cancelled":
                continue
            attendees = row.get("attendees", [])
            if not isinstance(attendees, list):
                return None
            if any(
                isinstance(person, dict) and str(person.get("email", "")).casefold() == needle
                for person in attendees
            ):
                return True
        else:
            fields = {
                "github": ("login",),
                "jira": ("accountId", "name", "key"),
                "miro": ("user_id", "userId"),
            }[instrument]
            if row.get("active") is False:
                continue
            if any(isinstance(row.get(key), str) and row[key].casefold() == needle for key in fields):
                return True
    return False


class AccessChecker:
    def __init__(self, gateway: Gateway, telegram: TelegramMembers, targets):
        self.gateway = gateway
        self.telegram = telegram
        self.targets = targets

    async def membership(self, case: str, obj: dict, profile: dict) -> bool | None:
        """True/False from the tool itself; None when the object or login cannot be checked."""
        instrument = obj["instrument"]
        target = obj.get("target") or self.targets(obj["id"]) or {}
        login = (profile.get("logins") or {}).get(LOGIN_KEYS[instrument], "")
        if not login:
            return None
        user = profile["user_id"]
        if instrument == "telegram":
            if "chat_id" not in target:
                return None
            permitted(case, "telegram", "getChatMember")
            try:
                status = await self.telegram.status(int(target["chat_id"]), int(login))
            except ValueError:
                return None
            if status not in TELEGRAM_MEMBER | {"left", "kicked", "restricted"}:
                return None
            return status in TELEGRAM_MEMBER
        calls = {
            "github": ("github", "list_repository_collaborators", ("owner", "repo"), {}),
            "jira": ("jira", "jira_search_assignable_users", ("project_key",), {"query": login}),
            "miro": ("miro", "content_item_list_roles", ("board_id",), {}),
            "google_calendar": ("calendar", "list-events", ("calendarId", "timeMin", "timeMax"), {}),
        }
        server, tool, keys, extra = calls[instrument]
        if any(key not in target for key in keys):
            return None
        arguments = {key: target[key] for key in keys}
        if instrument == "miro":
            arguments = {"contentItemId": target["board_id"]}
        return member_in_response(
            instrument,
            payload(await self.gateway.call(case, server, tool, {**arguments, **extra}, user_id=user)),
            login,
        )

    async def check(
        self, case: str, profile: dict, catalog: list[dict], marks: dict[str, str], now: datetime
    ) -> list[AccessItem]:
        items = []
        for obj in catalog:
            item = AccessItem(
                object_id=obj["id"],
                instrument=obj["instrument"],
                name=obj["name"],
                url=obj["url"],
                request_url=obj.get("request_url", ""),
                owner_id=obj["owner_id"],
                status="forbidden",
                reason=obj.get("reason", ""),
            )
            if obj.get("forbidden"):
                items.append(item)
                continue
            try:
                member = await self.membership(case, obj, profile)
                error = "" if member is not None else "no_target"
            except ToolDenied:
                raise
            except MCPError as exc:
                member, error = None, exc.code
            except TelegramUnavailable:
                member, error = None, "source_unavailable"
            requested = marks.get(obj["id"], "")
            if member:
                status = "granted"
            elif member is None:
                status = "unknown"
            elif requested:
                started = datetime.fromisoformat(requested)
                status = "stale" if now - started > STALE_AFTER else "requested"
            else:
                status = "missing"
            items.append(replace(item, status=status, requested_at=requested, error=error))
        return items
