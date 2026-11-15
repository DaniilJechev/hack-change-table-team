"""Controlled slow operations: acknowledgement, activity and task cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendChatAction

from buddy.bot import equator, messages, waiting
from buddy.contracts import Context, ExecutorResponse


def setup_deck():
    profile = Context("demo-daniil", "demo-orders", "intern", demo=True, session=42)
    message = SimpleNamespace(
        document=SimpleNamespace(file_name="good.pdf", file_id="file", file_size=3),
        chat=SimpleNamespace(id=42),
        bot=SimpleNamespace(download=AsyncMock(), send_chat_action=AsyncMock()),
        answer=AsyncMock(),
    )
    service = SimpleNamespace(
        runtime=Mock(return_value=object()),
        equator=SimpleNamespace(invalidate=AsyncMock()),
        equator_check=AsyncMock(return_value=(ExecutorResponse(result="Готово"), None)),
    )
    return message, service, profile


def answers(message):
    return [call.args[0] if call.args else call.kwargs["text"] for call in message.answer.await_args_list]


async def test_fr_h4_20_early_status_before_history_download_and_check(monkeypatch):
    message, service, profile = setup_deck()
    downloading, checking, refreshed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    download_release, check_release = asyncio.Event(), asyncio.Event()

    async def invalidate(*args):
        assert "получена" in answers(message)[0]

    async def download(*args, **kwargs):
        assert "получена" in answers(message)[0]
        downloading.set()
        await download_release.wait()
        kwargs["destination"].write(b"pdf")

    async def check(*args, **kwargs):
        assert "Проверяю презентацию" in answers(message)[-1]
        checking.set()
        await check_release.wait()
        return ExecutorResponse(result="Готово"), None

    async def action(*args, **kwargs):
        if message.bot.send_chat_action.await_count >= 2:
            refreshed.set()

    service.equator.invalidate.side_effect = invalidate
    service.equator_check.side_effect = check
    message.bot.download.side_effect = download
    message.bot.send_chat_action.side_effect = action
    monkeypatch.setattr(equator, "typing", lambda msg: waiting.typing(msg, interval=0.001))
    task = asyncio.create_task(equator.deck(message, service, profile))
    try:
        await asyncio.wait_for(downloading.wait(), 1)
        await asyncio.wait_for(refreshed.wait(), 1)
        assert not task.done()
        # Another coroutine can complete while the handler awaits external work.
        await asyncio.wait_for(message.answer("Локальное действие"), 1)
        assert answers(message)[-1] == "Локальное действие"
        download_release.set()
        await asyncio.wait_for(checking.wait(), 1)
        assert not task.done()
        check_release.set()
        await asyncio.wait_for(task, 1)
        assert "Готово" in answers(message)[-1]
        count = message.bot.send_chat_action.await_count
        await asyncio.sleep(0.01)
        assert message.bot.send_chat_action.await_count == count
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("error", [TimeoutError(), OSError("download failed")])
async def test_fr_h4_20_download_failure_has_no_check_or_success(error):
    message, service, profile = setup_deck()
    message.bot.download.side_effect = error
    await equator.deck(message, service, profile)
    assert "получена" in answers(message)[0]
    assert "Не удалось скачать" in answers(message)[-1]
    service.equator_check.assert_not_awaited()
    assert not any("Готово" in text for text in answers(message))


async def test_fr_h4_20_history_error_keeps_early_ack_and_stops_before_download():
    message, service, profile = setup_deck()
    service.equator.invalidate.side_effect = OSError("history down")
    await equator.deck(message, service, profile)
    assert "получена" in answers(message)[0]
    assert "История проверок недоступна" in answers(message)[-1]
    message.bot.download.assert_not_awaited()
    service.equator_check.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_nfr_perf_wait_1_stops_after_all_handler_outcomes(outcome):
    message, _, _ = setup_deck()
    started, release = asyncio.Event(), asyncio.Event()

    async def operation():
        async with waiting.typing(message, interval=0.001):
            started.set()
            await release.wait()
            if outcome == "error":
                raise ValueError("handler failed")

    task = asyncio.create_task(operation())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if outcome == "cancel":
            task.cancel()
        else:
            release.set()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if outcome == "error":
            assert isinstance(result, ValueError)
        elif outcome == "cancel":
            assert isinstance(result, asyncio.CancelledError)
        else:
            assert result is None
        count = message.bot.send_chat_action.await_count
        await asyncio.sleep(0.01)
        assert message.bot.send_chat_action.await_count == count
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_nfr_perf_wait_1_indicator_network_error_does_not_fail_handler():
    message, _, _ = setup_deck()
    retried = asyncio.Event()

    async def action(*args):
        if message.bot.send_chat_action.await_count == 1:
            raise TelegramNetworkError(
                method=SendChatAction(chat_id=42, action="typing"), message="synthetic failure"
            )
        retried.set()

    message.bot.send_chat_action.side_effect = action
    async with waiting.typing(message, interval=0.001):
        await asyncio.wait_for(retried.wait(), 1)


async def test_nfr_perf_wait_1_slow_indicator_does_not_hold_completion():
    message, _, _ = setup_deck()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def action(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    message.bot.send_chat_action.side_effect = action
    async with waiting.typing(message):
        await asyncio.wait_for(entered.wait(), 1)
    assert cancelled.is_set()


async def test_nfr_perf_wait_1_text_request_refreshes_until_result(monkeypatch):
    message, _, profile = setup_deck()
    message.from_user = SimpleNamespace(id=42)
    message.reply_to_message = None
    message.text = "Кто мой наставник?"
    refreshed, release = asyncio.Event(), asyncio.Event()

    async def handle(*args):
        await release.wait()
        return SimpleNamespace(case="team_q3"), ExecutorResponse(result="Ответ")

    async def action(*args):
        if message.bot.send_chat_action.await_count >= 2:
            refreshed.set()

    service = SimpleNamespace(
        awaiting_card={}, is_answering=Mock(return_value=False),
        router=SimpleNamespace(llm=object()), handle=AsyncMock(side_effect=handle),
    )
    message.bot.send_chat_action.side_effect = action
    monkeypatch.setattr(messages, "context", AsyncMock(return_value=profile))
    deliver = AsyncMock()
    monkeypatch.setattr(messages, "send", deliver)
    monkeypatch.setattr(messages, "typing", lambda msg: waiting.typing(msg, interval=0.001))
    task = asyncio.create_task(messages.route_text(message, service))
    try:
        await asyncio.wait_for(refreshed.wait(), 1)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 1)
        deliver.assert_awaited_once()
        assert deliver.await_args.args[2].result == "Ответ"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
