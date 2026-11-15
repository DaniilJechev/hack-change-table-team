from functools import partial

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from buddy.contracts import Action, ExecutorResponse
from buddy.orchestration.registry import TEAM_MENU, Context
from buddy.orchestration.service import AgentService
from buddy.render import to_html, to_plain


def team_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data="case:" + case)] for case, title in TEAM_MENU
        ]
    )


def keyboard(actions: list[Action]) -> InlineKeyboardMarkup | None:
    rows = []
    ratings = {}
    for action in actions:
        if not (action.url or action.data):
            continue
        button = (
            InlineKeyboardButton(text=action.label, url=action.url)
            if action.url
            else InlineKeyboardButton(text=action.label, callback_data=action.data)
        )
        if not action.url and (action.data or "").startswith(("brief:positive:", "brief:negative:")):
            brief_id = action.data.split(":", 2)[2]
            if brief_id not in ratings:
                ratings[brief_id] = []
                rows.append(ratings[brief_id])
            ratings[brief_id].append(button)
            ratings[brief_id].sort(key=lambda item: item.callback_data.startswith("brief:negative:"))
        else:
            rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def hide_brief_feedback(message: Message, brief_id: str) -> None:
    if message.reply_markup is None:
        return
    removed = {f"brief:{kind}:{brief_id}" for kind in ("positive", "negative", "read")}
    rows = [
        [button for button in row if button.callback_data not in removed]
        for row in message.reply_markup.inline_keyboard
    ]
    rows = [row for row in rows if row]
    await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def response_menu(case: str, response: ExecutorResponse | None = None):
    if response is not None and response.actions:
        return keyboard(response.actions)
    if case == "clarify":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Доступ к инструменту", callback_data="case:access_status")],
                [InlineKeyboardButton(text="Кто это и можно ли ему писать", callback_data="case:team_q3")],
            ]
        )
    if case == "out_of_scope":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Возможности", callback_data="case:help")],
                [InlineKeyboardButton(text="Спросить наставника", callback_data="mentor:contact")],
            ]
        )
    return team_menu() if case == "help" else None


def prefix(profile: Context) -> str:
    return "Демо: синтетический сценарий.\n" if profile.demo else ""


async def rich(method, response: ExecutorResponse, head: str = "", **kwargs):
    """FR-UI-02: HTML built by code; plain text when Telegram rejects the markup."""
    try:
        content = response.content()
        html = to_html(content.result, content.folded, content.links, head)
        return await method(text=html, parse_mode="HTML", **kwargs)
    except TelegramBadRequest:
        text = to_plain(content.result, content.folded, content.links, head)
        return await method(text=text, parse_mode=None, **kwargs)


async def send(
    message: Message,
    profile: Context,
    response: ExecutorResponse,
    case: str = "",
    agent_service: AgentService | None = None,
):
    try:
        sent = await rich(
            message.answer,
            response,
            prefix(profile),
            reply_markup=response_menu(case, response),
            disable_web_page_preview=True,
        )
    except Exception:
        # FR-H0-01: a brief that was not delivered stays unsent, so its changes are collected again.
        if agent_service is not None and response.brief_id:
            await agent_service.brief_delivered(response, profile, False)
        raise
    if agent_service is not None and response.brief_id:
        await agent_service.brief_delivered(response, profile, True)
        agent_service.remember_brief(message.chat.id, sent.message_id, response.brief_id)


async def deliver(message: Message, agent_service: AgentService, profile: Context, notes) -> list[str]:
    """Send messages to other participants; returns names that could not be reached."""
    missed = []
    for user_id, note in notes:
        chat = agent_service.chat_for(profile, user_id)
        if chat is None:
            missed.append(user_id)
            continue
        await rich(
            partial(message.bot.send_message, chat),
            note,
            prefix(profile),
            reply_markup=keyboard(note.actions),
            disable_web_page_preview=True,
        )
    return missed
