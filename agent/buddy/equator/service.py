"""H4: equator deck check (FR-H4-01..04). Mandatory checks and the report use no LLM."""

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from buddy.data.base import DataError
from buddy.data.mock_services import EQUATOR_ITEM
from buddy.equator.checks import Fact, group_label, judge, mentions, structure
from buddy.equator.history import DemoHistory
from buddy.equator.recommendations import recommend
from buddy.equator.slides import DeckError, Slide, detect, opened, pdf_slides, pptx_slides
from buddy.executors.context import Context
from buddy.mcp_hub.errors import FixtureMissing, MCPError
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
    team_id: str = ""
    demo: bool = False
    created_at: str = ""
    expires_at: str = ""
    digest: str = ""
    result: dict = field(default_factory=dict)
    delivery_file: str = "pending"
    delivery_report: str = "pending"
    current: bool = True
    generation: int = 0


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
    def __init__(self, upload_dir: str | None = None, pptx_gateway=None, *, settings=None, llm=None):
        self.upload_dir = Path(upload_dir or os.environ.get("PPTX_UPLOAD_DIR", "/presentations"))
        self.pptx_gateway = pptx_gateway
        self.checks: dict[str, Check] = {}
        self.settings, self.llm = settings, llm
        self.history = DemoHistory(getattr(settings, "h4_history_path", ""))
        self.locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def owner(context):
        return (
            f"demo:{context.session}:{context.team_id}:{context.user_id}"
            if context.demo
            else f"live:{context.team_id}:{context.user_id}"
        )

    async def records(self, context, runtime):
        rows = (
            self.history.history(self.owner(context))
            if context.demo
            else await runtime.data.equator_history(context.user_id)
        )
        now = datetime.now(UTC)
        self.checks = {
            key: value for key, value in self.checks.items() if datetime.fromisoformat(value.expires_at) > now
        }
        valid = []
        for row in rows:
            try:
                record = Check(**{k: v for k, v in row.items() if k in Check.__dataclass_fields__})
                if datetime.fromisoformat(record.expires_at) > now:
                    self.checks[record.id] = record
                    valid.append(record)
            except (TypeError, ValueError):
                continue
        return valid

    async def persist(self, record, context, runtime):
        value = asdict(record)
        saved = (
            self.history.save(self.owner(context), value)
            if context.demo
            else await runtime.data.equator_save(context.user_id, value)
        )
        if saved:
            record.current = saved.get("current", record.current)
            record.created_at = saved.get("created_at", record.created_at)
            record.expires_at = saved.get("expires_at", record.expires_at)
            record.delivery_file = saved.get("delivery_file", record.delivery_file)
            record.delivery_report = saved.get("delivery_report", record.delivery_report)
        self.checks[record.id] = record

    async def invalidate(self, context, runtime):
        for record in self.checks.values():
            if (
                record.user_id == context.user_id
                and record.team_id == context.team_id
                and record.demo == context.demo
                and record.session == context.session
            ):
                record.current = False
        if context.demo:
            return self.history.invalidate(self.owner(context))
        result = await runtime.data.equator_invalidate(context.user_id)
        return result["generation"]

    async def claim(self, record, part, context, runtime, *, retry_unknown=False):
        if context.demo:
            saved = self.history.claim(self.owner(context), record.id, part, retry_unknown=retry_unknown)
        else:
            saved = await runtime.data.equator_claim(
                context.user_id, record.id, part, retry_unknown=retry_unknown
            )
        setattr(record, "delivery_" + part, saved.get("delivery_" + part, "sending"))

    async def delivery(self, record, part, state, context, runtime):
        setattr(record, "delivery_" + part, state)
        if context.demo:
            await self.persist(record, context, runtime)
        else:
            await runtime.data.equator_delivery(context.user_id, record.id, part, state)

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
        projects = set()
        try:
            for obj in await runtime.data.access_catalog(context.user_id):
                if obj.get("instrument") == "jira" and not obj.get("forbidden"):
                    target = obj.get("target") or runtime.data.target(obj["id"]) or {}
                    if target.get("project_key"):
                        projects.add(target["project_key"].upper())
        except DataError:
            pass
        facts = []
        deadline = asyncio.get_running_loop().time() + 30
        for index, (key, items) in enumerate(grouped.items()):
            if (
                key.split("-")[0] not in projects
                or index >= 50
                or asyncio.get_running_loop().time() >= deadline
            ):
                facts.append(judge(key, items, None, "unchecked"))
                continue
            try:
                async with asyncio.timeout(max(0.01, deadline - asyncio.get_running_loop().time())):
                    result = await self._call(runtime, context, "jira", "jira_get_issue", {"issue_key": key})
                issue = result.get("structuredContent")
                if not isinstance(issue, dict):
                    issue = json.loads(result["content"][0]["text"])
                # Only an unambiguous typed not_found is treated as absence.
                if isinstance(issue, dict) and issue.get("error_code") == "not_found":
                    facts.append(judge(key, items, None, "missing"))
                else:
                    facts.append(judge(key, items, issue if isinstance(issue, dict) else None))
            except FixtureMissing:
                facts.append(judge(key, items, None, "missing" if context.demo else "unchecked"))
            except (MCPError, KeyError, IndexError, TypeError, ValueError, TimeoutError):
                facts.append(judge(key, items, None, "unchecked"))
        return facts

    async def check(self, name: str, data: bytes, context: Context, runtime: Runtime | None, *, file_id=""):
        if runtime is None:
            return ExecutorResponse(result="Проверка презентации сейчас недоступна. Попробуйте позже."), None
        lock = self.locks.setdefault(self.owner(context), asyncio.Lock())
        async with lock:
            return await self._check(name, data, context, runtime, file_id)

    async def _check(self, name, data, context, runtime, file_id=""):
        # A supported new upload supersedes the previous one even if reading fails.
        if not name.lower().endswith((".pdf", ".pptx")):
            return ExecutorResponse(result=DECK_ERRORS["format"]), None
        try:
            previous = await self.records(context, runtime)
            generation = await self.invalidate(context, runtime)
        except (DataError, OSError):
            return ExecutorResponse(
                result="История проверок недоступна. Попробуйте позже — старый допуск не используется."
            ), None
        try:
            slides = await self.slides(name, data, context, runtime)
        except DeckError as error:
            return ExecutorResponse(result=DECK_ERRORS[error.kind]), None
        try:
            profile = await runtime.data.profile(context.user_id)
            card = (await runtime.data.team_card(context.user_id))["document"]
        except (DataError, KeyError, TypeError):
            profile, card = None, None
        sections = structure(slides, profile, card)
        facts = await self.facts(slides, context, runtime)
        report, links, passed = render(sections, facts, len(slides))
        advice = await recommend(
            slides,
            self.llm,
            getattr(self.settings, "h4_recommended_max_slides", 10),
            getattr(self.settings, "h4_recommended_max_words_per_slide", 100),
        )
        issues = {"section:" + sec.id: sec.title for sec in sections if not sec.slides}
        issues.update(
            {
                "fact:" + fact.key: fact.key + " (" + fact.outcome + ")"
                for fact in facts
                if fact.outcome != "ok"
            }
        )
        if not any(f.completed_section for f in facts):
            issues["completed_keys"] = "Ключи выполненных задач"
        result = {
            "sections": [asdict(sec) for sec in sections],
            "facts": [asdict(f) for f in facts],
            "issues": issues,
            "recommendations": advice,
        }
        digest = hashlib.sha256(data).hexdigest()
        if previous:
            old = previous[0]
            before = old.result.get("issues", {})
            report += "\n\nИзменения с прошлой проверки:"
            for title, ids in (
                ("Исправлено", before.keys() - issues.keys()),
                ("Осталось", before.keys() & issues.keys()),
                ("Новое", issues.keys() - before.keys()),
            ):
                labels = [issues.get(key) or before[key] for key in sorted(ids)]
                report += "\n" + title + ": " + ("; ".join(labels) or "нет")
            if old.digest == digest and old.result.get("facts") != result["facts"]:
                report += "\nФайл не менялся; изменились сведения Jira или доступность источника."
        report += "\n\nРекомендации (не блокируют отправку):\n" + "\n".join(advice)
        report += (
            "\n\nУчебная структура синтетическая, не регламент Авито. "
            "Проверяю только извлекаемый текст; картинки, графики и оформление не оцениваю."
        )
        now = datetime.now(UTC)
        record = Check(
            uuid.uuid4().hex[:16],
            context.user_id,
            context.session,
            passed,
            report,
            links,
            file_name=name,
            file_id=file_id,
            team_id=context.team_id,
            demo=context.demo,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(days=getattr(self.settings, "h4_history_ttl_days", 7))).isoformat(),
            digest=digest,
            result=result,
            generation=generation,
        )
        try:
            await self.persist(record, context, runtime)
        except (DataError, OSError):
            return ExecutorResponse(
                result="Не удалось сохранить проверку. Пришлите презентацию ещё раз."
            ), None
        actions = (
            [Action(label="Отправить наставнику", data="equator:" + record.id)]
            if passed and record.current
            else []
        )
        return ExecutorResponse(result=report, links=links, actions=actions), record

    def find(self, check_id: str, context: Context) -> Check | None:
        record = self.checks.get(check_id)
        if (
            record is None
            or record.user_id != context.user_id
            or record.session != context.session
            or record.team_id != context.team_id
            or record.demo != context.demo
            or datetime.fromisoformat(record.expires_at) <= datetime.now(UTC)
        ):
            return None
        return record

    async def send(self, check_id: str, context: Context, runtime: Runtime | None):
        """FR-H4-04: mark the tracker item; returns the reply and (mentor_id, note) for delivery."""
        if runtime is not None:
            try:
                await self.records(context, runtime)
            except (DataError, OSError):
                return ExecutorResponse(result="История недоступна. Попробуйте позже."), None, None
        record = self.find(check_id, context)
        if record is None or runtime is None:
            return ExecutorResponse(result="Проверка не найдена. Пришлите презентацию ещё раз."), None, None
        if not record.current:
            return (
                ExecutorResponse(result="Эта версия устарела. Пришлите и проверьте актуальную презентацию."),
                None,
                None,
            )
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
        self.history.clear_session(session)
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
            lines.append(
                ("⚠️ Титул — не проверено: " if section.unchecked else "❌ Титул — на слайде 1 нет: ")
                + ", ".join(section.missing)
            )
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
            lines.append(
                f"⚠️ {fact.key} — {where}: не удалось проверить сведения Jira или утверждение; не проверено"
            )
    facts_ok = any(f.completed_section for f in facts) and all(fact.outcome == "ok" for fact in facts)
    if facts and not any(f.completed_section for f in facts):
        lines.append("❌ Укажите ключи Jira в разделе «Выполненные задачи»")
    passed = structure_ok and facts_ok
    lines.append("")
    lines.append(
        "Готово к показу наставнику: обязательные критерии пройдены."
        if passed
        else "Исправьте замечания в файле и пришлите новую версию — сам файл я не меняю."
    )
    return "\n".join(lines), links, passed
