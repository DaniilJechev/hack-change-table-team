"""H4: equator deck check (FR-H4-01..04). Mandatory checks and the report use no LLM."""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from buddy.data.base import DataError
from buddy.data.mock_services import EQUATOR_ITEM
from buddy.equator.checks import Fact, group_label, judge, mentions, structure
from buddy.equator.slides import DeckError, Slide, detect, opened, pdf_slides, pptx_slides
from buddy.executors.context import Context
from buddy.mcp_hub.errors import FixtureMissing, MCPError, RemoteToolError
from buddy.mcp_hub.policy import permitted
from buddy.runtime import Runtime
from buddy.schemas import Action, ExecutorResponse

CASE = "equator_check"
log = logging.getLogger(__name__)
DECK_ERRORS = {
    "format": "Пришлите презентацию в PDF или PPTX — другие форматы я не проверяю.",
    "too_large": "Файл больше 10 МиБ. Сожмите презентацию или пришлите PDF.",
    "too_many": "В презентации больше 30 слайдов — сократите её и пришлите снова.",
    "unreadable": "Не удалось прочитать файл. Проверьте, что он открывается, и пришлите снова.",
    "no_text": "В файле нет текста — похоже на скан. Пришлите PDF или PPTX с текстовым слоем.",
    "demo_unknown": (
        "В демо-режиме этот PPTX прочитать не удалось: сервис чтения PPTX не запущен. "
        "Пришлите PDF — его я проверю."
    ),
    "unavailable": "Сервис чтения PPTX сейчас недоступен. Пришлите PDF или попробуйте позже.",
}


@dataclass
class Check:
    id: str
    user_id: str
    session: int | None
    passed: bool
    report: str
    links: list[str]
    file_id: str = ""
    file_name: str = ""


def local_pptx_gateway():
    """Demo fallback: the local PowerPoint MCP container (no corporate account) when PPTX_MCP_URL is set."""
    url = os.environ.get("PPTX_MCP_URL", "")
    if not url:
        return None
    from buddy.mcp_hub.config import ServerConfig
    from buddy.mcp_hub.fixtures import FixtureStore
    from buddy.mcp_hub.hub import Hub

    # Live calls only; the fixture store is never written because record mode is off.
    return Hub(
        {"powerpoint": ServerConfig(name="powerpoint", url=url)},
        FixtureStore(Path(os.environ.get("PPTX_UPLOAD_DIR", "/presentations"))),
        demo_mode=False,
    )


class EquatorService:
    def __init__(self, upload_dir: str | None = None, pptx_gateway=None):
        self.upload_dir = Path(upload_dir or os.environ.get("PPTX_UPLOAD_DIR", "/presentations"))
        self.pptx_gateway = pptx_gateway
        self.checks: dict[str, Check] = {}

    async def _call(self, runtime: Runtime, context: Context, server: str, tool: str, arguments: dict):
        return await self._via(runtime.gateway, context, server, tool, arguments)

    @staticmethod
    async def _via(gateway, context: Context, server: str, tool: str, arguments: dict):
        permitted(CASE, server, tool)
        return await gateway.call(CASE, server, tool, arguments, user_id=context.user_id)

    async def _read(
        self, gateway, data: bytes, digest: str, context: Context, *, upload: bool
    ) -> list[Slide]:
        name = digest + ".pptx"
        path = None
        if upload:
            # The PowerPoint MCP reads this shared folder; the file is removed right after reading.
            path = self.upload_dir / name
            try:
                path.write_bytes(data)
            except OSError:
                raise DeckError("unavailable") from None
        try:
            arguments = {"file_path": name, "id": digest}
            opened(await self._via(gateway, context, "powerpoint", "open_presentation", arguments))
            arguments = {"presentation_id": digest, "include_slide_info": False}
            return pptx_slides(
                await self._via(gateway, context, "powerpoint", "extract_presentation_text", arguments)
            )
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    async def _pptx(self, data: bytes, digest: str, context: Context, runtime: Runtime) -> list[Slide]:
        try:
            return await self._read(runtime.gateway, data, digest, context, upload=not context.demo)
        except FixtureMissing:
            # Demo without a recorded answer: read it with the local container if it is running.
            if self.pptx_gateway is None:
                raise DeckError("demo_unknown") from None
        except MCPError:
            raise DeckError("unavailable") from None
        try:
            return await self._read(self.pptx_gateway, data, digest, context, upload=True)
        except MCPError:
            raise DeckError("demo_unknown") from None

    async def slides(self, name: str, data: bytes, context: Context, runtime: Runtime) -> list[Slide]:
        kind = detect(name, data)
        if len(data) > (10 << 20):
            raise DeckError("too_large")
        if kind == "pdf":
            return pdf_slides(data)
        return await self._pptx(data, hashlib.sha256(data).hexdigest(), context, runtime)

    async def facts(self, slides: list[Slide], context: Context, runtime: Runtime) -> list[Fact]:
        grouped: dict[str, list] = {}
        for item in mentions(slides):
            grouped.setdefault(item.key, []).append(item)
        facts = []
        for key, items in grouped.items():
            try:
                result = await self._call(runtime, context, "jira", "jira_get_issue", {"issue_key": key})
                issue = json.loads(result["content"][0]["text"])
                facts.append(judge(key, items, issue if isinstance(issue, dict) else None))
            except (FixtureMissing, RemoteToolError):
                # Jira answered for this key without an issue; in demo the synthetic Jira is the fixtures.
                facts.append(judge(key, items, None, "missing"))
            except (MCPError, KeyError, IndexError, TypeError, ValueError):
                facts.append(judge(key, items, None, "unchecked"))
        return facts

    async def check(self, name: str, data: bytes, context: Context, runtime: Runtime | None):
        if runtime is None:
            return ExecutorResponse(result="Проверка презентации сейчас недоступна. Попробуйте позже."), None
        try:
            slides = await self.slides(name, data, context, runtime)
        except DeckError as error:
            return ExecutorResponse(result=DECK_ERRORS[error.kind]), None
        sections = structure(slides)
        facts = await self.facts(slides, context, runtime)
        report, links, passed = render(sections, facts, len(slides))
        digest = hashlib.sha256(data + context.user_id.encode()).hexdigest()[:16]
        record = Check(digest, context.user_id, context.session, passed, report, links, file_name=name)
        self.checks[digest] = record
        log.info("equator check: slides=%d passed=%s facts=%d", len(slides), passed, len(facts))
        actions = [Action(label="Отправить наставнику", data="equator:" + digest)] if passed else []
        return ExecutorResponse(result=report, links=links, actions=actions), record

    def find(self, check_id: str, context: Context) -> Check | None:
        record = self.checks.get(check_id)
        if record is None or record.user_id != context.user_id or record.session != context.session:
            return None
        return record

    async def send(self, check_id: str, context: Context, runtime: Runtime | None):
        """FR-H4-04: mark the tracker item; returns the reply and (mentor_id, note) for delivery."""
        record = self.find(check_id, context)
        if record is None or runtime is None:
            return ExecutorResponse(result="Проверка не найдена. Пришлите презентацию ещё раз."), None, None
        if not record.passed:
            return ExecutorResponse(result="Сначала исправьте замечания и пришлите новую версию."), None, None
        try:
            item = await runtime.data.mark_progress(context.user_id, EQUATOR_ITEM)
            profile = await runtime.data.profile(context.user_id)
        except DataError as error:
            return (
                ExecutorResponse(result="Не удалось отметить этап в трекере. Код: " + error.kind + "."),
                None,
                None,
            )
        when = (item.get("completed_at") or "")[:10]
        reply = ExecutorResponse(
            result=(
                f"Этап «{item.get('title', 'Презентация к экватору')}» "
                f"в трекере прогресса (имитация): ✅ отмечен{' ' + when if when else ''}."
            )
        )
        name = profile.get("display_name") or context.user_id
        note = ExecutorResponse(
            result=f"{name} прислал(а) презентацию к экватору. AI-buddy подтвердил обязательные критерии.\n\n"
            + record.report,
            links=record.links,
        )
        return reply, profile.get("mentor_id"), note

    def clear(self, session: int) -> None:
        for key in [key for key, value in self.checks.items() if value.session == session]:
            self.checks.pop(key, None)


def render(sections, facts: list[Fact], count: int) -> tuple[str, list[str], bool]:
    lines = [f"Проверка презентации, слайдов: {count}", "", "Структура — обязательный критерий:"]
    structure_ok = all(section.slides for section in sections)
    for section in sections:
        if section.slides:
            numbers = ", ".join(map(str, section.slides))
            lines.append(f"✅ {section.title} — слайд {numbers}")
        elif section.id == "title":
            lines.append("❌ Титул — на слайде 1 нет: " + ", ".join(section.missing))
        else:
            lines.append(f"❌ {section.title} — нет раздела. Заголовки: " + "; ".join(section.synonyms[:3]))
    lines += ["", "Факты (Jira) — обязательный критерий:"]
    links: list[str] = []
    if not facts:
        lines.append("❌ В презентации нет ключей задач Jira (например, ORD-118)")
    for fact in facts:
        where = "слайд " + ", ".join(map(str, fact.slides))
        mark = ""
        if fact.url:
            links.append(fact.url)
            mark = f" [{len(links)}]"
        if fact.outcome == "ok":
            said = f", статус «{fact.status}»" if fact.said else ""
            lines.append(f"✅ {fact.key} — {where}{said}{mark}")
        elif fact.outcome == "mismatch":
            lines.append(
                f"⚠️ {fact.key} — {where}: на слайде «{group_label(fact.said)}», в Jira «{fact.status}»{mark}"
            )
        elif fact.outcome == "missing":
            lines.append(f"❌ {fact.key} — {where}: задача не найдена в Jira")
        else:
            lines.append(f"⚠️ {fact.key} — {where}: Jira недоступна, не проверено")
    facts_ok = bool(facts) and all(fact.outcome == "ok" for fact in facts)
    passed = structure_ok and facts_ok
    lines.append("")
    lines.append(
        "Готово к показу наставнику: обязательные критерии пройдены."
        if passed
        else "Исправьте замечания в файле и пришлите новую версию — сам файл я не меняю."
    )
    return "\n".join(lines), links, passed
