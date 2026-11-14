"""Go backend client: reads through buddy-mcp (MCP), writes only our data through REST."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from buddy.data.base import DataError

TIMEOUT = 5
STATUS = {400: "invalid", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}
TOOL_ERRORS = {
    "Доступ запрещён": "forbidden",
    "Данные не найдены": "not_found",
    "Некорректный запрос": "invalid",
}
McpCall = Callable[[str, str, dict], Awaitable[dict]]


def check_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid BACKEND_URL")
    return url.rstrip("/")


def tool_payload(result) -> dict:
    """MCP CallToolResult (SDK object or dict) → JSON object; errors map to safe kinds."""
    value = result.model_dump(mode="json", by_alias=True) if hasattr(result, "model_dump") else result
    texts = [item.get("text", "") for item in value.get("content") or [] if isinstance(item, dict)]
    if value.get("isError"):
        raise DataError(next((TOOL_ERRORS[text] for text in texts if text in TOOL_ERRORS), "unavailable"))
    if isinstance(value.get("structuredContent"), dict):
        return value["structuredContent"]
    for text in texts:
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            return decoded
    raise DataError("unavailable")


class BackendData:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        targets: dict | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        mcp: McpCall | None = None,
    ):
        self.base = check_url(url)
        self.key = key
        self.targets = targets or {}
        self.client = httpx.AsyncClient(
            base_url=self.base,
            headers={"X-Buddy-Key": key},
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.mcp = mcp or self._mcp_call

    def target(self, object_id: str) -> dict | None:
        # External IDs are not part of the stage-two backend catalog yet (see stage-3 OpenSpec change).
        return self.targets.get(object_id)

    async def close(self) -> None:
        await self.client.aclose()

    async def _mcp_call(self, user_id: str, tool: str, arguments: dict) -> dict:  # pragma: no cover - network
        headers = {"X-Buddy-Key": self.key, "X-Buddy-User": user_id}
        async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT, trust_env=False) as client:
            async with streamable_http_client(self.base + "/mcp", http_client=client) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    return tool_payload(await session.call_tool(tool, arguments))

    async def _read(self, user_id: str, tool: str, arguments: dict | None = None) -> dict:
        outcome = "ok"
        try:
            async with asyncio.timeout(TIMEOUT):
                return await self.mcp(user_id, tool, arguments or {})
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except DataError as error:
            outcome = error.kind
            raise
        except Exception:
            outcome = "unavailable"
            raise DataError("unavailable") from None
        finally:
            await self.record_mcp_call(
                {
                    "case": "buddy_read",
                    "server": "buddy",
                    "tool": tool,
                    "user_id": user_id,
                    "outcome": outcome,
                }
            )

    async def record_mcp_call(self, call: dict) -> None:
        import logging

        try:
            await self._rest(
                "POST",
                "/api/v1/mcp-calls",
                call["user_id"],
                {
                    "id": "mc-" + uuid.uuid4().hex,
                    **{key: call[key] for key in ("case", "server", "tool", "outcome")},
                },
            )
        except DataError:
            logging.getLogger(__name__).warning("MCP audit metadata could not be saved")

    async def _rest(self, method: str, path: str, user_id: str, body: dict | None = None) -> dict:
        try:
            async with asyncio.timeout(TIMEOUT):
                response = await self.client.request(
                    method, path, headers={"X-Buddy-User": user_id}, json=body
                )
        except (httpx.HTTPError, TimeoutError):
            raise DataError("unavailable") from None
        if response.status_code not in (200, 201, 204):
            raise DataError(STATUS.get(response.status_code, "unavailable"))
        if response.status_code == 204 or not response.content:
            return {}
        try:
            value = response.json()
        except ValueError:
            raise DataError("unavailable") from None
        if not isinstance(value, dict):
            raise DataError("unavailable")
        return value

    async def profile(self, user_id: str) -> dict:
        return await self._read(user_id, "user_profile_get")

    async def team_card(self, user_id: str) -> dict:
        return await self._read(user_id, "team_card_get")

    async def faq_search(self, user_id: str, text: str) -> list[dict]:
        items = list((await self._read(user_id, "team_faq_search", {"text": text})).get("items") or [])
        for item in items:
            source = item.get("source_path", "")
            if source.startswith("/api/v1/faq/"):
                item["source_path"] = self.base + source
        return items

    async def access_catalog(self, user_id: str) -> list[dict]:
        return list((await self._read(user_id, "access_catalog_get")).get("items") or [])

    async def access_marks(self, user_id: str) -> dict[str, str]:
        items = (await self._read(user_id, "access_marks_get")).get("items") or []
        return {item["object_id"]: item["requested_at"] for item in items}

    async def request_access(self, user_id: str, object_id: str) -> dict:
        return await self._rest("POST", "/api/v1/mock/access-requests", user_id, {"object_id": object_id})

    async def access_requests(self, user_id: str) -> list[dict]:
        return (await self._rest("GET", "/api/v1/mock/access-requests", user_id))["items"]

    async def resolve_access_request(
        self, user_id: str, request_id: str, status: str, reason: str = ""
    ) -> dict:
        return await self._rest(
            "PATCH",
            "/api/v1/mock/access-requests/" + quote(request_id, safe=""),
            user_id,
            {"status": status, "reason": reason},
        )

    async def progress(self, user_id: str) -> list[dict]:
        return (await self._rest("GET", "/api/v1/mock/progress", user_id))["items"]

    async def mark_progress(self, user_id: str, item_id: str, completed: bool = True) -> dict:
        return await self._rest(
            "PUT", "/api/v1/mock/progress/" + quote(item_id, safe=""), user_id, {"completed": completed}
        )

    async def mark_requested(self, user_id: str, object_id: str) -> str:
        value = await self._rest("PUT", "/api/v1/access-marks/" + quote(object_id, safe=""), user_id)
        return value.get("requested_at", "")

    async def interns(self, user_id: str) -> list[dict]:
        return list((await self._read(user_id, "interns_list")).get("items") or [])

    async def save_snapshot(self, user_id: str, snapshot_id: str, objects: list[dict]) -> None:
        await self._rest("POST", "/api/v1/access-snapshots", user_id, {"id": snapshot_id, "objects": objects})

    async def create_question(self, user_id: str, question_id: str, text: str, assigned_to: str) -> dict:
        body = {"id": question_id, "text": text, "assigned_to": assigned_to}
        return await self._rest("POST", "/api/v1/questions", user_id, body)

    async def question(self, user_id: str, question_id: str) -> dict:
        return await self._rest("GET", "/api/v1/questions/" + quote(question_id, safe=""), user_id)

    async def answer_question(self, user_id: str, question_id: str, text: str) -> dict:
        path = "/api/v1/questions/" + quote(question_id, safe="") + "/answer"
        return await self._rest("POST", path, user_id, {"text": text})

    async def rate_question(self, user_id: str, question_id: str, helpful: bool) -> None:
        path = "/api/v1/questions/" + quote(question_id, safe="")
        await self._rest("PATCH", path, user_id, {"helpful": helpful})

    async def publish_faq(self, user_id: str, faq_id: str, question_id: str) -> dict:
        body = {"id": faq_id, "question_id": question_id, "consent": True}
        return await self._rest("POST", "/api/v1/faq", user_id, body)

    async def record_bot_answer(
        self,
        user_id: str,
        question_id: str,
        text: str,
        answer: str,
        sources: list[str],
        capability: str = "h2",
    ) -> dict:
        await self._rest(
            "POST", "/api/v1/questions", user_id, {"id": question_id, "text": text, "capability": capability}
        )
        return await self._rest(
            "POST",
            "/api/v1/questions/" + quote(question_id, safe="") + "/bot-answer",
            user_id,
            {"text": answer, "sources": sources},
        )

    async def rate_bot_answer(self, user_id: str, question_id: str, helpful: bool) -> None:
        await self._rest(
            "PATCH",
            "/api/v1/questions/" + quote(question_id, safe="") + "/bot-answer",
            user_id,
            {"helpful": helpful},
        )

    async def flags(self, user_id: str) -> dict[str, bool]:
        return await self._rest("GET", "/api/v1/flags", user_id)

    async def upload_team_card(self, user_id: str, source: bytes) -> dict:
        if len(source) > 1 << 20:
            raise DataError("invalid")
        try:
            async with asyncio.timeout(TIMEOUT):
                response = await self.client.post(
                    "/api/v1/team-card",
                    content=source,
                    headers={"X-Buddy-User": user_id, "Content-Type": "application/yaml"},
                )
            if response.status_code != 200:
                raise DataError(STATUS.get(response.status_code, "unavailable"))
            value = response.json()
            if not isinstance(value, dict):
                raise DataError("unavailable")
            return value
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise DataError("unavailable") from None

    async def latest_snapshot(self, user_id: str) -> dict | None:
        try:
            return await self._rest("GET", "/api/v1/access-snapshots/latest", user_id)
        except DataError as error:
            if error.kind == "not_found":
                return None
            raise

    async def save_brief(self, user_id: str, brief_id: str, items: list[dict]) -> dict:
        stored = [
            {
                **item,
                **({"source": "google_calendar"} if item.get("source") == "calendar" else {}),
                **({"priority_class": "information"} if item.get("priority_class") == "info" else {}),
            }
            for item in items
        ]
        return await self._rest("POST", "/api/v1/briefs", user_id, {"id": brief_id, "items": stored})

    async def brief_status(
        self, user_id: str, brief_id: str, status: str, calendar: dict | None = None, at: str = ""
    ) -> None:
        path = "/api/v1/briefs/" + quote(brief_id, safe="")
        body = {"status": status}
        if status == "sent" and calendar is not None:
            body["calendar"] = calendar
        await self._rest("PATCH", path, user_id, body)

    async def brief_event(self, user_id: str, brief_id: str, kind: str, item_id: str = "") -> None:
        path = "/api/v1/briefs/" + quote(brief_id, safe="") + "/events"
        body = {"id": "e-" + uuid.uuid4().hex[:16], "kind": kind}
        if item_id:
            body["item_id"] = item_id
        await self._rest("POST", path, user_id, body)

    async def brief(self, user_id: str, brief_id: str) -> dict:
        record = await self._rest("GET", "/api/v1/briefs/" + quote(brief_id, safe=""), user_id)
        for item in record.get("items", []):
            if item.get("source") == "google_calendar":
                item["source"] = "calendar"
            if item.get("priority_class") == "information":
                item["priority_class"] = "info"
        return record

    async def last_brief_sent(self, user_id: str) -> str | None:
        return (await self._rest("GET", "/api/v1/brief-state", user_id))["last_sent"]

    async def brief_seen(self, user_id: str) -> set[str]:
        return set((await self._rest("GET", "/api/v1/brief-state", user_id))["seen"])

    async def calendar_snapshot(self, user_id: str) -> dict | None:
        return (await self._rest("GET", "/api/v1/brief-state", user_id))["calendar"]

    async def brief_stats(self, user_id: str) -> list[dict]:
        return (await self._rest("GET", "/api/v1/brief-stats", user_id))["items"]

    async def brief_settings(self, user_id: str) -> dict:
        return await self._rest("GET", "/api/v1/brief-settings", user_id)

    async def set_brief_settings(self, user_id: str, **changes) -> dict:
        return await self._rest("PATCH", "/api/v1/brief-settings", user_id, changes)

    async def equator_history(self, user_id: str) -> list[dict]:
        return (await self._rest("GET", "/api/v1/equator/checks", user_id))["items"]

    async def equator_save(self, user_id: str, record: dict) -> dict:
        path = "/api/v1/equator/checks/" + quote(record["id"], safe="")
        return await self._rest("PUT", path, user_id, record)

    async def equator_invalidate(self, user_id: str) -> dict:
        return await self._rest("POST", "/api/v1/equator/invalidate", user_id)

    async def equator_clear(self, user_id: str) -> dict:
        return await self._rest("DELETE", "/api/v1/equator/checks", user_id)

    async def equator_claim(
        self, user_id: str, check_id: str, part: str, retry_unknown: bool = False
    ) -> dict:
        path = "/api/v1/equator/checks/" + quote(check_id, safe="") + "/claim"
        return await self._rest("POST", path, user_id, {"part": part, "retry_unknown": retry_unknown})

    async def equator_delivery(self, user_id: str, check_id: str, part: str, state: str) -> dict:
        path = "/api/v1/equator/checks/" + quote(check_id, safe="") + "/delivery"
        return await self._rest("PATCH", path, user_id, {"part": part, "state": state})

    async def mentor_chat(self, user_id: str) -> int | None:
        return (await self._rest("GET", "/api/v1/equator/mentor-chat", user_id))["chat_id"]
