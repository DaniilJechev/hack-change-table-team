"""FR-H0-UI-1: one-row feedback, persisted vote hides buttons without losing More."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from tests.test_stage_two_bot import Harness

from buddy.data.base import DataError


async def press(app, brief, kind, *, more=True):
    markup = brief.reply_markup.model_copy(deep=True)
    if not more:
        markup.inline_keyboard = [markup.inline_keyboard[-1]]
    row = markup.inline_keyboard[-1]
    data = next(button.callback_data for button in row if button.callback_data.startswith(f"brief:{kind}:"))
    brief_id = data.split(":", 2)[2]
    message_id = next(key[1] for key, value in app.service.brief_messages.items() if value == brief_id)
    app.index += 1
    user = User(id=42, is_bot=False, first_name="Synthetic")
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        text=brief.text,
        reply_markup=markup,
    )
    query = CallbackQuery(
        id=str(app.index), from_user=user, chat_instance="synthetic", message=message, data=data
    )
    await app.dispatcher.feed_update(app.bot, Update(update_id=app.index, callback_query=query))
    return brief_id


@pytest.mark.parametrize("kind", ["positive", "negative"])
@pytest.mark.parametrize("more", [True, False])
async def test_fr_h0_ui_1_saved_vote_hides_feedback_and_keeps_other_buttons(kind, more):
    app = Harness()
    try:
        await app.send("/demo")
        brief = await app.send("/brief")
        rows = brief.reply_markup.inline_keyboard
        assert [[button.text for button in row] for row in rows] == [["Ещё 3"], ["👍", "👎"]]
        assert all("brief:read:" not in (button.callback_data or "") for row in rows for button in row)
        brief_id = await press(app, brief, kind, more=more)
        assert len(app.session.edits) == 1
        edited = app.session.edits[0].reply_markup
        if more:
            assert [[button.text for button in row] for row in edited.inline_keyboard] == [["Ещё 3"]]
        else:
            assert edited is None
        record = await app.service.demo.sessions[42].data.brief("demo-daniil", brief_id)
        assert record["read_at"] and record["events"][-1]["kind"] == kind
        assert "Спасибо за оценку брифа" in app.session.sent[-1].text
    finally:
        await app.bot.session.close()


@pytest.mark.parametrize("kind", ["positive", "negative"])
async def test_fr_h0_ui_1_failed_vote_keeps_feedback(kind):
    app = Harness()
    try:
        await app.send("/demo")
        brief = await app.send("/brief")
        app.service.demo.sessions[42].data.brief_event = AsyncMock(side_effect=DataError("unavailable"))
        await press(app, brief, kind)
        assert app.session.edits == []
        assert "Не удалось сохранить" in app.session.sent[-1].text
    finally:
        await app.bot.session.close()
