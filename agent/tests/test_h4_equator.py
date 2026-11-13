"""H4: equator deck check (openspec/changes/h4-equator-check-draft). One test per scenario at least."""

import dataclasses
import hashlib
import io
import json
import runpy
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Document, Message, Update, User
from pypdf import PdfWriter

from buddy.demo.controller import DemoController
from buddy.equator.checks import mentions, section_of, structure, title_markers
from buddy.equator.service import EquatorService
from buddy.equator.slides import DeckError, Slide, detect, pdf_slides
from buddy.mcp_hub.errors import SourceUnavailable, ToolDenied
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.policy import CASE_TOOLS, READ_TOOLS, permitted
from tests.test_stage_two_bot import Harness

ROOT = Path(__file__).resolve().parents[2]
DECKS = ROOT / "fixtures" / "equator"


def demo():
    controller = DemoController(True)
    controller.command(1)
    return controller.context(1), controller.runtime(1)


async def run(name: str, data: bytes | None = None, runtime=None):
    context, demo_runtime = demo()
    service = EquatorService()
    response, record = await service.check(
        name, data if data is not None else (DECKS / name).read_bytes(), context, runtime or demo_runtime
    )
    return service, response, record


def deck(*slides: tuple[str, str]) -> list[Slide]:
    return [Slide(number, title, title + "\n" + body) for number, (title, body) in enumerate(slides, 1)]


TITLE = ("Экваторная встреча", "Стажёр команды «Заказы»\nНаставник: Анна\nСентябрь – ноябрь 2026")


# FR-H4-01 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["good.pdf", "good.pptx"])
async def test_fr_h4_01_pdf_and_pptx_give_numbered_slides(name):
    context, runtime = demo()
    slides = await EquatorService().slides(name, (DECKS / name).read_bytes(), context, runtime)
    assert [slide.number for slide in slides] == [1, 2, 3, 4, 5, 6]
    assert slides[1].title == "Цели стажировки"
    assert "ORD-115 — готово" in slides[2].text


def test_fr_h4_01_pptx_wrapper_keeps_two_read_tools(monkeypatch, tmp_path):
    """The wrapper runs against a stub of the pinned upstream registry: write tools are removed."""
    calls = []

    def opener(file_path, id=None):
        calls.append(file_path)
        return {"presentation_id": id, "message": "Opened presentation from " + file_path, "slide_count": 1}

    names = ["open_presentation", "extract_presentation_text", "save_presentation", "add_slide"]
    names += [f"write_{n}" for n in range(8)]
    registry = {
        name: types.SimpleNamespace(fn=opener if name == "open_presentation" else None) for name in names
    }
    stub = types.SimpleNamespace(
        app=types.SimpleNamespace(_tool_manager=types.SimpleNamespace(_tools=registry)), presentations={}
    )
    monkeypatch.setitem(sys.modules, "ppt_mcp_server", stub)
    monkeypatch.setenv("PPTX_ROOT", str(tmp_path))
    runpy.run_path(str(ROOT / "mcp/powerpoint/entrypoint.py"))
    assert set(registry) == {"open_presentation", "extract_presentation_text"}
    tool = registry["open_presentation"].fn
    assert tool("deck.pptx", "x")["message"] == "Opened presentation deck.pptx"
    for outside in ["../deck.pptx", str(tmp_path.parent / "deck.pptx"), "deck.txt"]:
        assert "outside" in tool(outside)["error"]
    assert len(calls) == 1
    # The agent side keeps the same two tools and denies anything else.
    assert READ_TOOLS["powerpoint"] == CASE_TOOLS["equator_check"]["powerpoint"]
    with pytest.raises(ToolDenied):
        permitted("equator_check", "powerpoint", "save_presentation")
    catalog = json.loads((ROOT / "fixtures/step_1/powerpoint/catalog.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in catalog["tools"]} == READ_TOOLS["powerpoint"]
    assert all(item["annotations"]["readOnlyHint"] for item in catalog["tools"])


def test_fr_h4_01_pptx_wrapper_refuses_changed_upstream(monkeypatch):
    stub = types.SimpleNamespace(
        app=types.SimpleNamespace(_tool_manager=types.SimpleNamespace(_tools={"add_slide": None})),
        presentations={},
    )
    monkeypatch.setitem(sys.modules, "ppt_mcp_server", stub)
    with pytest.raises(SystemExit):
        runpy.run_path(str(ROOT / "mcp/powerpoint/entrypoint.py"))


async def test_fr_h4_01_pptx_in_demo_reads_fixtures_and_unknown_asks_pdf():
    _, response, record = await run("good.pptx")
    assert record is not None and record.passed
    unknown = (DECKS / "good.pptx").read_bytes() + b"changed"
    _, response, record = await run("other.pptx", unknown)
    assert record is None and "Пришлите PDF" in response.result


class LocalPowerPoint:
    """Stands in for the local PowerPoint MCP container: answers like the recorded good.pptx."""

    def __init__(self, upload_dir: Path, *, fail=False):
        good = hashlib.sha256((DECKS / "good.pptx").read_bytes()).hexdigest()
        self.store = FixtureStore(ROOT / "fixtures", step="step_1")
        self.good, self.upload_dir, self.fail = good, upload_dir, fail
        self.seen: list[tuple[str, list[str]]] = []

    async def call(self, case, server, tool, arguments, *, user_id):
        self.seen.append((tool, [path.name for path in self.upload_dir.iterdir()]))
        if self.fail:
            raise SourceUnavailable("container is not running")
        if tool == "open_presentation":
            return self.store.load(server, tool, {"file_path": self.good + ".pptx", "id": self.good})
        return self.store.load(server, tool, {"presentation_id": self.good, "include_slide_info": False})


async def test_fr_h4_01_unknown_pptx_in_demo_uses_local_container(tmp_path):
    context, runtime = demo()
    local = LocalPowerPoint(tmp_path)
    service = EquatorService(upload_dir=str(tmp_path), pptx_gateway=local)
    data = (DECKS / "good.pptx").read_bytes() + b"changed"
    response, record = await service.check("my.pptx", data, context, runtime)
    assert record is not None and record.passed and response.actions
    name = hashlib.sha256(data).hexdigest() + ".pptx"
    # The file is in the shared folder while the container reads it and is removed afterwards.
    assert local.seen == [("open_presentation", [name]), ("extract_presentation_text", [name])]
    assert list(tmp_path.iterdir()) == []
    down = EquatorService(upload_dir=str(tmp_path), pptx_gateway=LocalPowerPoint(tmp_path, fail=True))
    response, record = await down.check("my.pptx", data, context, runtime)
    assert record is None and "Пришлите PDF" in response.result


def test_fr_h4_01_local_container_only_when_configured(monkeypatch):
    from buddy.equator.service import local_pptx_gateway

    monkeypatch.delenv("PPTX_MCP_URL", raising=False)
    assert local_pptx_gateway() is None
    monkeypatch.setenv("PPTX_MCP_URL", "http://powerpoint-mcp:8000/mcp")
    hub = local_pptx_gateway()
    assert set(hub.servers) == {"powerpoint"} and not hub.demo_mode and not hub.record_mode


async def test_fr_h4_01_scan_without_text_is_not_checked():
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=400)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DeckError) as error:
        pdf_slides(stream.getvalue())
    assert error.value.kind == "no_text"
    _, response, record = await run("scan.pdf", stream.getvalue())
    assert record is None and "текстовым слоем" in response.result


@pytest.mark.parametrize(
    "name, data, kind",
    [
        ("plan.docx", b"PK\x03\x04docx", "format"),
        ("fake.pdf", b"PK\x03\x04", "format"),
        ("deck.pptx", b"%PDF-1.7", "format"),
        ("broken.pdf", b"%PDF-1.7 broken", "unreadable"),
    ],
)
async def test_fr_h4_01_other_format_is_refused(name, data, kind):
    if kind == "format":
        with pytest.raises(DeckError):
            detect(name, data)
    _, response, record = await run(name, data)
    assert record is None and not response.actions


async def test_fr_h4_01_too_large_and_too_many_slides():
    _, response, _ = await run("big.pdf", b"%PDF-" + b"0" * (10 << 20))
    assert "10 МиБ" in response.result
    writer = PdfWriter()
    for _ in range(31):
        writer.add_blank_page(width=10, height=10)
    stream = io.BytesIO()
    writer.write(stream)
    _, response, _ = await run("long.pdf", stream.getvalue())
    assert "30 слайдов" in response.result


# FR-H4-02 ---------------------------------------------------------------------------------------


def test_fr_h4_02_all_six_sections_with_synonyms():
    slides = deck(
        TITLE, ("Цели", ""), ("Что сделано", ""), ("Итоги", ""), ("Трудности", ""), ("Что дальше", "")
    )
    sections = structure(slides)
    assert [section.slides for section in sections] == [[1], [2], [3], [4], [5], [6]]


def test_fr_h4_02_missing_section_is_reported():
    slides = deck(TITLE, ("Цели", ""), ("Что сделано", ""), ("Трудности", ""), ("Планы", ""))
    results = next(section for section in structure(slides) if section.id == "results")
    assert results.slides == [] and "итоги" in results.synonyms


@pytest.mark.parametrize(
    "heading, section",
    [
        ("3. ВЫПОЛНЕННЫЕ ЗАДАЧИ:", "done"),
        ("2) Цели стажировки 🎯", "goals"),
        ("Сложности и чему научился", "lessons"),
        ("Целиком о себе", None),
        ("Планы до конца стажировки", "plans"),
    ],
)
def test_fr_h4_02_numbering_and_case_are_ignored(heading, section):
    assert section_of(heading) == section


def test_fr_h4_02_incomplete_title_names_missing_markers():
    slide = Slide(1, "Привет", "Привет\nДаниил\nКоманда «Заказы»")
    assert title_markers(slide) == ["наставник", "стажёр или стажировка", "период"]
    title = structure([slide])[0]
    assert title.slides == [] and "наставник" in title.missing


# FR-H4-03 ---------------------------------------------------------------------------------------


async def test_fr_h4_03_status_match_mismatch_and_missing_issue():
    _, response, record = await run("bad.pdf")
    text = response.result
    assert "✅ ORD-118 — слайд 3, статус «In Progress» [1]" in text
    assert "⚠️ ORD-121 — слайд 3: на слайде «готово», в Jira «To Do» [2]" in text
    assert "❌ ORD-999 — слайд 3: задача не найдена в Jira" in text
    # Links come from Jira payloads, not from the slide text.
    assert response.links == [
        "https://example.invalid/jira/browse/ORD-118",
        "https://example.invalid/jira/browse/ORD-121",
    ]
    assert record is not None and not record.passed


def test_fr_h4_03_status_words_are_read_from_the_key_line():
    slides = deck(("Выполненные задачи", "ORD-1 — на ревью\nORD-2 сделано, теперь в работе ORD-3\nORD-4"))
    said = [(item.key, item.said) for item in mentions(slides)]
    assert said == [("ORD-1", "review"), ("ORD-2", "done"), ("ORD-3", "done"), ("ORD-4", None)]


async def test_fr_h4_03_jira_unavailable_is_not_checked_and_has_no_button():
    _, runtime = demo()

    class Down:
        async def call(self, case, server, tool, arguments, *, user_id):
            raise SourceUnavailable("down")

    _, response, record = await run("good.pdf", runtime=dataclasses.replace(runtime, gateway=Down()))
    assert "Jira недоступна, не проверено" in response.result
    assert record is not None and not record.passed and not response.actions


async def test_fr_h4_03_deck_without_keys_fails_facts():
    service = EquatorService()
    context, runtime = demo()
    slides = deck(TITLE, ("Цели", ""), ("Что сделано", ""), ("Итоги", ""), ("Трудности", ""), ("Планы", ""))
    assert await service.facts(slides, context, runtime) == []


# FR-H4-04 ---------------------------------------------------------------------------------------


class DeckHarness(Harness):
    async def upload(self, name: str, data: bytes, monkeypatch, *, user=42):
        async def download(*args, **kwargs):
            kwargs["destination"].write(data)

        monkeypatch.setattr(Bot, "download", AsyncMock(side_effect=download))
        self.index += 1
        message = Message(
            message_id=self.index,
            date=datetime.now(UTC),
            chat=Chat(id=user, type="private"),
            from_user=User(id=user, is_bot=False, first_name="Synthetic"),
            document=Document(file_id="tg-" + name, file_unique_id=name, file_name=name, file_size=len(data)),
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=self.index, message=message))
        return self.session.sent[-1]


async def test_fr_h4_04_bad_deck_lists_remarks_without_button(monkeypatch):
    app = DeckHarness()
    try:
        await app.send("/demo")
        reply = await app.upload("bad.pptx", (DECKS / "bad.pptx").read_bytes(), monkeypatch)
        assert "❌ Результаты" in reply.text and "⚠️ ORD-121" in reply.text
        assert reply.reply_markup is None
        progress = await app.service.demo.runtime(42).data.progress("demo-daniil")
        assert progress[0]["completed"] is False
    finally:
        await app.bot.session.close()


async def test_fr_h4_04_good_deck_button_marks_tracker_and_reaches_mentor(monkeypatch):
    app = DeckHarness()
    sent_document = AsyncMock()
    monkeypatch.setattr(Bot, "send_document", sent_document)
    try:
        await app.send("/demo")
        reply = await app.upload("good.pdf", (DECKS / "good.pdf").read_bytes(), monkeypatch)
        assert "Готово к показу наставнику" in reply.text
        button = reply.reply_markup.inline_keyboard[0][0]
        assert button.text == "Отправить наставнику" and button.callback_data.startswith("equator:")
        await app.callback(button.callback_data)
        assert "Отправлено наставнику" in app.session.sent[-1].text
        assert "✅ отмечен" in app.session.sent[-1].text
        # Demo: the mentor's copy (file + report) is shown in the presenter's chat.
        assert "AI-buddy подтвердил" in app.session.sent[-2].text
        sent_document.assert_awaited_once()
        assert sent_document.await_args.args == (42, "tg-good.pdf")
        data = app.service.demo.runtime(42).data
        first = (await data.progress("demo-daniil"))[0]
        assert first["completed"] is True
        await app.callback(button.callback_data)
        assert (await data.progress("demo-daniil"))[0]["completed_at"] == first["completed_at"]
        # A forged or foreign check id does nothing.
        await app.callback("equator:0000000000000000")
        assert "не найдена" in app.session.sent[-1].text
    finally:
        await app.bot.session.close()


async def test_fr_h4_04_demo_uses_seed_data_without_backend(monkeypatch):
    import httpx

    async def no_http(*args, **kwargs):
        raise AssertionError("HTTP is forbidden in demo")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_http)
    service, response, record = await run("good.pptx")
    context, runtime = demo()
    service.checks[record.id].session = context.session
    reply, mentor, note = await service.send(record.id, context, runtime)
    assert mentor == "demo-anna" and "✅ отмечен" in reply.result and note.links == response.links


async def test_fr_h4_04_failed_check_cannot_be_sent():
    service, _, record = await run("bad.pdf")
    context, runtime = demo()
    reply, mentor, note = await service.send(record.id, context, runtime)
    assert note is None and "исправьте" in reply.result.lower()


async def test_fr_h4_04_yaml_still_goes_to_teamcard(monkeypatch):
    app = DeckHarness()
    try:
        await app.send("/demo")
        reply = await app.upload("card.yaml", b"team: x", monkeypatch)
        assert "/teamcard" in reply.text
        reply = await app.upload("notes.docx", b"PK\x03\x04", monkeypatch)
        assert "PDF или PPTX" in reply.text
    finally:
        await app.bot.session.close()
