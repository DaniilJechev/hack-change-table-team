"""Normalised brief item (FR-H0-02) and the ranking classes (FR-H0-03)."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from buddy.schemas import Contract

Source = Literal["github", "jira", "calendar"]
PriorityClass = Literal["blocker", "people", "deadline", "info"]
CLASS_ORDER = {"blocker": 0, "people": 1, "deadline": 2, "info": 3}
CLASS_ICON = {"blocker": "🔴", "people": "🟠", "deadline": "🟡", "info": "⚪"}
SOURCE_TITLE = {"github": "Git", "jira": "Jira", "calendar": "Календарь"}
# Types that need the intern's own action; the main action of the day is the first of them.
ACTION_TEXT = {
    "pr_changes_requested": "исправить замечания в {title}",
    "pr_review_requested": "сделать ревью: {title}",
    "pr_comment": "ответить на комментарий в {title}",
    "jira_mention": "ответить на упоминание в {title}",
    "jira_comment": "прочитать комментарий в {title}",
    "jira_due": "закрыть до срока: {title}",
    "jira_assigned": "взять в работу: {title}",
    "meeting_moved": "учесть перенос встречи: {title}",
    "meeting_new": "подготовиться ко встрече: {title}",
}


class BriefItem(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    source: Source
    type: str = Field(pattern=r"^[a-z_]{1,40}$")
    title: str = Field(min_length=1, max_length=200)
    url: str = ""
    priority_class: PriorityClass
    related_task: str = ""
    before: str = ""
    after: str = ""
    created_at: datetime

    def line(self) -> str:
        change = f" ({self.before} → {self.after})" if self.before and self.after else ""
        return f"{CLASS_ICON[self.priority_class]} {SOURCE_TITLE[self.source]}: {self.title}{change}"


def valid_url(url: str) -> bool:
    """FR-H0-LINK-2: a link must point to a concrete object, checked by code."""
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        return False
    parsed = urlsplit(url)
    return bool(
        parsed.scheme in {"https", "http"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.path.strip("/")
    )
