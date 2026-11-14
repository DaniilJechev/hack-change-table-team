"""H4 completion: strict facts, advice, versions, persistence and delivery recovery."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from tests.test_h4_equator import DECKS, DeckHarness, demo

from buddy.data.base import DataError
from buddy.equator.checks import Mention, judge, mentions, structure
from buddy.equator.recommendations import recommend
from buddy.equator.service import EquatorService
from buddy.equator.slides import Slide
from buddy.llm.client import FakeLLM
from buddy.mcp_hub.errors import RemoteToolError


@pytest.mark.parametrize(
    "issue",
    [
        {},
        {"key": "OTHER-1"},
        {"key": "ORD-1", "status": "Done", "url": "javascript:alert(1)"},
        {"key": "ORD-1", "status": "Done", "url": "https://example.invalid"},
    ],
)
def test_fr_h4_07_malformed_source_never_passes(issue):
    assert judge("ORD-1", [Mention("ORD-1", 3, "done", True)], issue).outcome == "unchecked"


async def test_fr_h4_03_06_goals_reference_and_heading_status():
    context, runtime = demo()
    service = EquatorService()
    slides = [
        Slide(1, "Цели", "Цели\nРазобраться с ORD-118"),
        Slide(2, "Выполненные задачи", "Выполненные задачи\nORD-115"),
    ]
    facts = await service.facts(slides, context, runtime)
    assert all(f.outcome == "ok" for f in facts)
    assert facts[0].said is None and facts[1].said == "done"


async def test_fr_h4_07_foreign_project_is_not_read_and_remote_error_not_missing():
    context, runtime = demo()
    service = EquatorService()
    gateway = SimpleNamespace(call=AsyncMock(side_effect=RemoteToolError()))
    runtime = dataclasses.replace(runtime, gateway=gateway)
    facts = await service.facts([Slide(1, "Задачи", "PAY-1 готово\nORD-118 готово")], context, runtime)
    assert [f.outcome for f in facts] == ["unchecked", "unchecked"]
    assert gateway.call.await_count == 1


@pytest.mark.parametrize(
    "body,expected",
    [
        ("ORD-115 готово; ORD-118 в работе", ["done", "progress"]),
        ("В работе ORD-118", ["progress"]),
        ("ORD-118 статус: неизвестен", ["ambiguous"]),
    ],
)
def test_fr_h4_06_explicit_status_and_unknown(body, expected):
    assert [m.said for m in mentions([Slide(1, "Цели", body)])] == expected


async def test_fr_h4_02_05_only_headings_cannot_pass():
    context, runtime = demo()
    slides = [Slide(1, "Титул", "Даниил\nЗаказы\nАнна\nсентябрь — ноябрь 2026")]
    slides += [
        Slide(i, t, t)
        for i, t in enumerate(["Цели", "Выполненные задачи", "Результаты", "Сложности", "Планы"], 2)
    ]
    result = structure(slides, await runtime.data.profile(context.user_id), runtime.data.card)
    assert result[0].slides == [1] and not any(s.slides for s in result[1:])


@pytest.mark.parametrize("words,slides,warning", [(100, 10, False), (101, 10, True), (100, 11, True)])
async def test_fr_h4_09_configurable_boundaries(words, slides, warning):
    deck = [Slide(i, "", " ".join(["слово"] * words)) for i in range(1, slides + 1)]
    result = await recommend(deck, None)
    assert any("больше" in line for line in result) == warning
    result = await recommend(deck, None, 15, 150)
    assert not any("больше" in line for line in result)


async def test_fr_h4_08_images_only_warn_and_text_section_required():
    result = await recommend([Slide(1, "", ""), Slide(2, "Результаты", "Результаты")], None)
    assert any("Слайд 1" in line and "не проверено" in line for line in result)
    assert not structure([Slide(1, "Результаты", "Результаты")])[3].slides


@pytest.mark.parametrize(
    "reply",
    [
        {"remarks": [{"slide": 99, "quote": "Цель", "text": "Добавь результат"}]},
        {"remarks": [{"slide": 1, "quote": "выдумано", "text": "Добавь результат"}]},
        {"bad": []},
    ],
)
async def test_fr_h4_10_invalid_advice_falls_back(reply):
    result = await recommend([Slide(1, "Цели", "Цель: ускорить")], FakeLLM(json.dumps(reply)))
    assert result[-1] == "Сопоставление целей с результатами не выполнено."


async def test_fr_h4_10_valid_advice_is_grounded_and_empty_valid():
    slides = [Slide(1, "Цели", "Цель: ускорить")]
    model = FakeLLM(
        json.dumps(
            {"remarks": [{"slide": 1, "quote": "Цель: ускорить", "text": "Добавь измеримый результат"}]},
            ensure_ascii=False,
        )
    )
    assert "Слайд 1" in (await recommend(slides, model))[-1]
    assert "замечаний нет" in (await recommend(slides, FakeLLM('{"remarks":[]}')))[-1]


async def test_fr_h4_12_13_persistent_demo_versions_and_expiry(tmp_path):
    context, runtime = demo()
    settings = SimpleNamespace(h4_history_path=str(tmp_path / "history.sqlite"), h4_history_ttl_days=7)
    first = EquatorService(settings=settings)
    _, bad = await first.check("bad.pdf", (DECKS / "bad.pdf").read_bytes(), context, runtime)
    restarted = EquatorService(settings=settings)
    reply, good = await restarted.check("good.pdf", (DECKS / "good.pdf").read_bytes(), context, runtime)
    assert good.passed and good.id != bad.id and "Исправлено:" in reply.result
    assert len(await restarted.records(context, runtime)) == 2
    good.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await restarted.persist(good, context, runtime)
    records = await restarted.records(context, runtime)
    assert all(r.id != good.id for r in records)
    assert good.id not in restarted.checks


async def test_fr_h4_14_bad_or_unreadable_upload_invalidates_prior():
    context, runtime = demo()
    service = EquatorService()
    _, good = await service.check("good.pdf", (DECKS / "good.pdf").read_bytes(), context, runtime)
    await service.check("broken.pdf", b"%PDF-broken", context, runtime)
    response, _, note = await service.send(good.id, context, runtime)
    assert note is None and "устарела" in response.result


async def test_fr_h4_13_same_file_is_new_attempt():
    context, runtime = demo()
    service = EquatorService()
    _, first = await service.check("good.pdf", (DECKS / "good.pdf").read_bytes(), context, runtime)
    _, second = await service.check("good.pdf", (DECKS / "good.pdf").read_bytes(), context, runtime)
    assert first.id != second.id and first.digest == second.digest
    assert not (await service.records(context, runtime))[1].current


async def test_fr_h4_16_partial_report_requires_explicit_warned_retry(monkeypatch):
    app = DeckHarness()
    monkeypatch.setattr(Bot, "send_document", AsyncMock())
    try:
        await app.send("/demo")
        reply = await app.upload("good.pdf", (DECKS / "good.pdf").read_bytes(), monkeypatch)
        check_id = reply.reply_markup.inline_keyboard[0][0].callback_data.split(":")[1]
        context = app.service.demo.context(42)
        runtime = app.service.runtime(context)
        record = app.service.equator.find(check_id, context)
        record.report = "\n".join(["Строка отчёта " + str(i) + " " + ("текст " * 30) for i in range(60)])
        await app.service.equator.persist(record, context, runtime)
        bad = TelegramBadRequest(method=SendMessage(chat_id=42, text="x"), message="bad")
        mentor_send = AsyncMock(side_effect=[object(), bad, bad])
        monkeypatch.setattr(Bot, "send_message", mentor_send)
        # Harness callback invokes to_mentor using its own authenticated message.
        await app.callback("equator:" + check_id)
        stored = (await app.service.equator.records(context, runtime))[0]
        assert stored.delivery_file == "sent" and stored.delivery_report == "unknown"
        assert "копию" in app.session.sent[-1].text
        assert (
            app.session.sent[-1].reply_markup.inline_keyboard[0][0].callback_data
            == "equator:retry:" + check_id
        )
        count = mentor_send.await_count
        await app.callback("equator:" + check_id)
        assert mentor_send.await_count == count
    finally:
        await app.bot.session.close()


async def test_fr_h4_15_foreign_owner_and_expired_record():
    context, runtime = demo()
    service = EquatorService()
    _, record = await service.check("good.pdf", (DECKS / "good.pdf").read_bytes(), context, runtime)
    assert service.find(record.id, dataclasses.replace(context, session=99)) is None
    with pytest.raises(DataError):
        await service.claim(record, "file", dataclasses.replace(context, session=99), runtime)


async def test_fr_h4_14_download_failure_invalidates_old_button(monkeypatch):
    app = DeckHarness()
    try:
        await app.send("/demo")
        reply = await app.upload("good.pdf", (DECKS / "good.pdf").read_bytes(), monkeypatch)
        context = app.service.demo.context(42)
        check_id = reply.reply_markup.inline_keyboard[0][0].callback_data.split(":")[1]
        # Upload core already invalidated at receipt, before a later failing download.
        from aiogram.types import Chat, Document, Message, User

        from buddy.bot.equator import deck

        message = Message(
            message_id=991,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            from_user=User(id=42, is_bot=False, first_name="Synthetic"),
            document=Document(file_id="broken", file_unique_id="broken", file_name="new.pdf", file_size=100),
        )
        message = message.as_(app.bot)
        monkeypatch.setattr(Bot, "download", AsyncMock(side_effect=RuntimeError()))
        await deck(message, app.service, context)
        response, _, note = await app.service.equator_send(check_id, context)
        assert note is None and "устарела" in response.result
    finally:
        await app.bot.session.close()


async def test_fr_h4_16_delivery_state_save_failure_is_unknown(monkeypatch):
    app = DeckHarness()
    monkeypatch.setattr(Bot, "send_document", AsyncMock())
    try:
        await app.send("/demo")
        reply = await app.upload("good.pdf", (DECKS / "good.pdf").read_bytes(), monkeypatch)
        check_id = reply.reply_markup.inline_keyboard[0][0].callback_data.split(":")[1]
        monkeypatch.setattr(app.service.equator, "delivery", AsyncMock(side_effect=DataError()))
        await app.callback("equator:" + check_id)
        assert "Исход неизвестен" in app.session.sent[-1].text
        assert "копию" in app.session.sent[-1].text
        assert (
            app.session.sent[-1].reply_markup.inline_keyboard[0][0].callback_data.startswith("equator:retry:")
        )
    finally:
        await app.bot.session.close()


def test_fr_h4_14_reset_does_not_resurrect_inflight_payload(tmp_path):
    from tests.test_h4_review import OWNER, record

    from buddy.equator.history import DemoHistory

    history = DemoHistory(str(tmp_path / "history.sqlite"))
    generation = history.invalidate(OWNER)
    history.clear_session(42)
    history.save(OWNER, record(generation))
    assert history.history(OWNER) == []
