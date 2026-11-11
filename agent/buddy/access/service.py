"""H1 executors: access status, task analysis, monitoring, «я запросил», mentor summary. Read-only."""

import json
import uuid
from datetime import datetime

from buddy.access.checker import AccessChecker, AccessItem, payload
from buddy.data.base import DataError
from buddy.executors.context import Context
from buddy.mcp_hub.errors import MCPError
from buddy.runtime import Runtime
from buddy.schemas import Action, ExecutorResponse

TITLES = {
    "github": "GitHub",
    "jira": "Jira",
    "miro": "Miro",
    "google_calendar": "Google Calendar",
    "telegram": "Telegram",
}
ICONS = {
    "granted": "✅",
    "missing": "⬜",
    "requested": "⏳",
    "stale": "⏰",
    "forbidden": "⛔",
    "unknown": "❔",
}
SNAPSHOT_STATUSES = {"granted", "missing", "requested", "stale", "forbidden"}
UNAVAILABLE = "Данные доступов сейчас недоступны. Попробуйте позже."


def _short(value: str, limit: int = 40) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _since(requested_at: str, now: datetime) -> str:
    hours = int((now - datetime.fromisoformat(requested_at)).total_seconds() // 3600)
    return f"{hours // 24} дн." if hours >= 48 else f"{max(hours, 0)} ч"


class AccessService:
    def __init__(self):
        self.previous: dict[str, dict[str, str]] = {}

    @staticmethod
    def key(context: Context) -> str:
        return f"{context.user_id}:{context.session}" if context.session is not None else context.user_id

    async def _people(self, context: Context, runtime: Runtime) -> dict[str, str]:
        try:
            card = (await runtime.data.team_card(context.user_id))["document"]
        except DataError:
            return {}
        return {person["user_id"]: person.get("name", person["user_id"]) for person in card.get("people", [])}

    async def _items(self, case: str, context: Context, runtime: Runtime, only: set[str] | None = None):
        profile = await runtime.data.profile(context.user_id)
        catalog = await runtime.data.access_catalog(context.user_id)
        if only is not None:
            catalog = [item for item in catalog if item["id"] in only]
        marks = await runtime.data.access_marks(context.user_id)
        checker = AccessChecker(runtime.gateway, runtime.telegram, runtime.data.target)
        return await checker.check(case, profile, catalog, marks, runtime.clock())

    def _render(self, items: list[AccessItem], people: dict[str, str], now: datetime, title: str):
        lines, actions, links = [title], [], []
        for item in items:
            owner = people.get(item.owner_id, item.owner_id)
            head = f"{ICONS[item.status]} {TITLES[item.instrument]} — {item.name}"
            if item.status == "granted":
                line = head + ": доступ есть"
            elif item.status == "missing":
                line = head + f": доступа нет. Запросить у владельца: {owner}"
                if item.request_url:
                    actions.append(Action(label=_short("Где запросить: " + item.name), url=item.request_url))
                actions.append(
                    Action(label=_short("Я запросил: " + item.name), data="mark:" + item.object_id)
                )
            elif item.status == "requested":
                line = head + f": запрошено {_since(item.requested_at, now)} назад, ждём выдачи"
            elif item.status == "stale":
                line = head + f": доступа нет уже {_since(item.requested_at, now)} после запроса"
                actions.append(
                    Action(
                        label=_short("Черновик напоминания: " + item.name), data="remind:" + item.object_id
                    )
                )
            elif item.status == "forbidden":
                line = head + ": стажёрам не выдаётся. " + (item.reason or "Обсудите с наставником.")
            else:
                line = head + ": не удалось проверить, попробуйте позже"
            lines.append(line)
            links.append(item.url)
        lines.append("Бот только проверяет доступы и ничего не меняет в сервисах.")
        return ExecutorResponse(result="\n".join(lines), actions=actions, links=list(dict.fromkeys(links)))

    async def _restore(self, context: Context, runtime: Runtime) -> None:
        key = self.key(context)
        if key in self.previous:
            return
        try:
            snapshot = await runtime.data.latest_snapshot(context.user_id)
        except DataError:
            return
        if snapshot:
            self.previous[key] = {
                item["object_id"]: item["status"]
                for item in snapshot.get("objects", [])
                if item.get("status") in SNAPSHOT_STATUSES
            }

    async def _remember(
        self, context: Context, runtime: Runtime, items: list[AccessItem]
    ) -> list[tuple[AccessItem, str]]:
        """Store the new snapshot; return notifications for status changes since the previous one."""
        await self._restore(context, runtime)
        key = self.key(context)
        previous = self.previous.get(key, {})
        current = {
            item.object_id: item.status if item.status != "unknown" else previous.get(item.object_id, "")
            for item in items
        }
        current = {key_: value for key_, value in current.items() if value}
        notes = []
        for item in items:
            before = previous.get(item.object_id)
            if before is None:
                continue
            if item.status == "granted" and before != "granted":
                notes.append((item, f"Доступ к {TITLES[item.instrument]} ({item.name}) появился ✅"))
            elif item.status == "stale" and before != "stale":
                notes.append((item, f"Доступ к {TITLES[item.instrument]} ({item.name}) не выдан больше 48 ч"))
        self.previous[key] = current
        objects = [
            {"object_id": object_id, "status": status}
            for object_id, status in current.items()
            if status in SNAPSHOT_STATUSES
        ]
        if objects:
            try:
                await runtime.data.save_snapshot(context.user_id, "s-" + uuid.uuid4().hex[:16], objects)
            except DataError:
                pass
        return notes

    async def status(self, context: Context, runtime: Runtime) -> ExecutorResponse:
        try:
            items = await self._items("access_status", context, runtime)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE)
        await self._remember(context, runtime, items)
        people = await self._people(context, runtime)
        return self._render(items, people, runtime.clock(), "Ваши доступы:")

    async def task(self, context: Context, runtime: Runtime, task: str) -> ExecutorResponse:
        if not task:
            return ExecutorResponse(result="О какой задаче речь? Напишите ключ задачи, например ORD-118.")
        try:
            issue = payload(
                await runtime.gateway.call(
                    "access_task_analysis",
                    "jira",
                    "jira_get_issue",
                    {"issue_key": task.upper()},
                    user_id=context.user_id,
                )
            )
        except MCPError:
            return ExecutorResponse(
                result=f"Не удалось получить задачу {task.upper()} из Jira. Попробуйте позже."
            )
        issue = issue if isinstance(issue, dict) else {}
        text = json.dumps(
            [issue.get("summary", ""), issue.get("description", "")], ensure_ascii=False
        ).casefold()
        try:
            catalog = await runtime.data.access_catalog(context.user_id)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE)
        mentioned = {
            item["id"]
            for item in catalog
            if any(
                word in text
                for word in (item.get("target") or runtime.data.target(item["id"]) or {}).get("keywords", [])
            )
        }
        note = ""
        if not mentioned:
            mentioned = {item["id"] for item in catalog}
            note = "В задаче не нашёл явных упоминаний инструментов — показываю все доступы команды.\n"
        try:
            items = await self._items("access_task_analysis", context, runtime, mentioned)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE)
        people = await self._people(context, runtime)
        response = self._render(
            items, people, runtime.clock(), note + f"Для задачи {task.upper()} нужны доступы:"
        )
        if all(item.status in {"granted", "forbidden"} for item in items):
            response.result = response.result.replace(
                "нужны доступы:", "доступов хватает ✅ (запрещённые объекты не требуются):", 1
            )
        if issue.get("url"):
            response.links.insert(0, issue["url"])
        return response

    async def mark(self, context: Context, runtime: Runtime, object_id: str) -> ExecutorResponse:
        try:
            catalog = {item["id"]: item for item in await runtime.data.access_catalog(context.user_id)}
            requested = await runtime.data.mark_requested(context.user_id, object_id)
        except DataError as exc:
            if exc.kind == "not_found":
                return ExecutorResponse(result="Этот объект нельзя отметить: он не найден или запрещён.")
            return ExecutorResponse(result=UNAVAILABLE)
        name = catalog.get(object_id, {}).get("name", object_id)
        when = datetime.fromisoformat(requested).strftime("%d.%m %H:%M") if requested else "сейчас"
        return ExecutorResponse(
            result=f"Отметил: доступ к «{name}» запрошен ({when}). Проверю, когда он появится, и напишу."
        )

    async def reminder(self, context: Context, runtime: Runtime, object_id: str) -> ExecutorResponse:
        try:
            profile = await runtime.data.profile(context.user_id)
            catalog = {item["id"]: item for item in await runtime.data.access_catalog(context.user_id)}
            marks = await runtime.data.access_marks(context.user_id)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE)
        item = catalog.get(object_id)
        if item is None or item.get("forbidden"):
            return ExecutorResponse(result="Для этого объекта напоминание не нужно.")
        people = await self._people(context, runtime)
        owner = people.get(item["owner_id"], item["owner_id"])
        since = marks.get(object_id)
        date = datetime.fromisoformat(since).strftime("%d.%m") if since else "недавно"
        draft = (
            f"{owner}, добрый день! {date} я запросил доступ к «{item['name']}» ({item['url']}) "
            f"для задач стажировки. Подскажите, пожалуйста, есть ли новости? — {profile['display_name']}"
        )
        return ExecutorResponse(
            result="Черновик напоминания (скопируйте и отправьте сами — бот ничего не отправляет):\n\n"
            + draft
        )

    async def interns(self, context: Context, runtime: Runtime) -> ExecutorResponse:
        try:
            interns = await runtime.data.interns(context.user_id)
        except DataError as exc:
            if exc.kind == "forbidden":
                return ExecutorResponse(result="Сводка стажёров доступна только наставнику или тимлиду.")
            return ExecutorResponse(result=UNAVAILABLE)
        if not interns:
            return ExecutorResponse(result="Стажёров в вашей зоне пока нет.")
        lines = ["Доступы стажёров:"]
        for intern in interns:
            snapshot = intern.get("snapshot") or {}
            objects = snapshot.get("objects") or []
            if not objects:
                lines.append(f"{intern['display_name']} — проверок ещё не было")
                continue
            total = [item for item in objects if item["status"] != "forbidden"]
            granted = sum(item["status"] == "granted" for item in total)
            waiting = [item["object_id"] for item in total if item["status"] in {"requested", "stale"}]
            line = f"{intern['display_name']} — {granted}/{len(total)}"
            if waiting:
                line += ", ждёт: " + ", ".join(waiting)
            lines.append(line + (" ✅" if granted == len(total) else ""))
        return ExecutorResponse(result="\n".join(lines))

    async def monitor(self, context: Context, runtime: Runtime) -> ExecutorResponse | None:
        """Scheduled check (access_events): only status changes are reported."""
        key = self.key(context)
        try:
            items = await self._items("access_events", context, runtime)
        except DataError:
            return None
        await self._restore(context, runtime)
        first = key not in self.previous
        notes = await self._remember(context, runtime, items)
        if first or not notes:
            return None
        links = [item.url for item, _ in notes if item.status == "granted"]
        actions = [
            Action(label=_short("Черновик напоминания: " + item.name), data="remind:" + item.object_id)
            for item, _ in notes
            if item.status == "stale"
        ]
        return ExecutorResponse(result="\n".join(text for _, text in notes), actions=actions, links=links)
