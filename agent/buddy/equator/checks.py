"""FR-H4-02/03: mandatory checks by rules only. Synthetic structure, not an Avito template."""

import re
from dataclasses import dataclass, field

from buddy.equator.slides import Slide

SECTIONS = (
    ("title", "Титул", ()),
    (
        "goals",
        "Цели стажировки",
        ("цели стажировки", "цели", "мои цели", "цели на стажировку", "ожидания от стажировки"),
    ),
    (
        "done",
        "Выполненные задачи",
        (
            "выполненные задачи",
            "что сделано",
            "что я сделал",
            "сделанные задачи",
            "проделанная работа",
            "мои задачи",
        ),
    ),
    (
        "results",
        "Результаты",
        ("результаты", "итоги", "что изменилось", "эффект для команды", "результаты в цифрах"),
    ),
    (
        "lessons",
        "Сложности и чему научился",
        ("сложности и чему научился", "сложности", "трудности", "чему научился", "чему я научился", "уроки"),
    ),
    (
        "plans",
        "Планы до конца стажировки",
        ("планы до конца стажировки", "планы", "дальнейшие планы", "следующие шаги", "что дальше"),
    ),
)
MONTHS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "ма[йя]",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
)
MONTH = re.compile(r"(?<!\w)(?:" + "|".join(MONTHS) + r")\w*")
DATE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b")
TITLE_MARKERS = {
    "наставник": re.compile(r"наставни"),
    "команда": re.compile(r"команд"),
    "стажёр или стажировка": re.compile(r"стажер|стажиров"),
    "период": None,
}
GROUPS = {
    "done": ("Готово", ("готово", "сделано", "выполнено", "закрыта", "done"), ("done", "closed", "resolved")),
    "progress": ("В работе", ("в работе", "в процессе", "делаю", "in progress"), ("in progress",)),
    "review": ("На ревью", ("на ревью", "на проверке", "review"), ("in review", "code review")),
    "todo": ("В планах", ("в планах", "запланировано", "to do", "todo"), ("to do", "open", "backlog")),
}
KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"^\s*\d+\s*[.)]\s*", "", text)
    text = re.sub(r"^[\W_]+|[\W_]+$", "", text)
    return re.sub(r"\s+", " ", text)


@dataclass
class Section:
    id: str
    title: str
    synonyms: tuple[str, ...]
    slides: list[int] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def section_of(title: str) -> str | None:
    heading = normalize(title)
    best = None
    for section_id, _, synonyms in SECTIONS:
        for synonym in synonyms:
            fits = heading == synonym or (heading.startswith(synonym) and not heading[len(synonym)].isalnum())
            if fits and (best is None or len(synonym) > len(best[1])):
                best = (section_id, synonym)
    return best[0] if best else None


def title_markers(slide: Slide) -> list[str]:
    """Missing title markers of slide 1; three of four are enough."""
    text = normalize(slide.text.replace("\n", " "))
    missing = []
    for name, pattern in TITLE_MARKERS.items():
        found = (
            len(MONTH.findall(text)) >= 2 or len(DATE.findall(text)) >= 2
            if pattern is None
            else bool(pattern.search(text))
        )
        if not found:
            missing.append(name)
    return missing


def structure(slides: list[Slide]) -> list[Section]:
    sections = {sid: Section(sid, title, synonyms) for sid, title, synonyms in SECTIONS}
    if slides:
        missing = title_markers(slides[0])
        if len(missing) <= 1:
            sections["title"].slides.append(1)
        else:
            sections["title"].missing = missing
    for slide in slides:
        found = section_of(slide.title)
        if found:
            sections[found].slides.append(slide.number)
    return list(sections.values())


def _word(word: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)")


WORDS = sorted(
    ((_word(word), group) for group, (_, words, _) in GROUPS.items() for word in words),
    key=lambda item: -len(item[0].pattern),
)


def said_status(line: str) -> str | None:
    """Status group named in the line, the first one by position."""
    text = normalize(KEY.sub(" ", line))
    hits = [(match.start(), group) for pattern, group in WORDS for match in [pattern.search(text)] if match]
    return min(hits)[1] if hits else None


def jira_group(status: str) -> str | None:
    name = (status or "").strip().lower()
    return next((group for group, (_, _, names) in GROUPS.items() if name in names), None)


@dataclass
class Mention:
    key: str
    slide: int
    said: str | None


def mentions(slides: list[Slide]) -> list[Mention]:
    found = []
    for slide in slides:
        for line in slide.lines:
            for key in dict.fromkeys(KEY.findall(line)):
                found.append(Mention(key, slide.number, said_status(line)))
    return found


@dataclass
class Fact:
    key: str
    slides: list[int]
    outcome: str  # ok, mismatch, missing, unchecked
    said: str | None = None
    status: str = ""
    url: str = ""


def judge(key: str, items: list[Mention], issue: dict | None, error: str = "") -> Fact:
    """issue=None with error 'missing' or 'unchecked'; otherwise compare claimed groups."""
    slides = sorted({item.slide for item in items})
    if issue is None:
        return Fact(key, slides, error or "unchecked")
    status = issue.get("status")
    status = status.get("name", "") if isinstance(status, dict) else str(status or "")
    url = issue.get("url") if isinstance(issue.get("url"), str) else ""
    actual = jira_group(status)
    for item in items:
        if item.said and item.said != actual:
            return Fact(key, slides, "mismatch", item.said, status, url)
    return Fact(key, slides, "ok", next((i.said for i in items if i.said), None), status, url)


def group_label(group: str | None) -> str:
    return GROUPS[group][0].lower() if group in GROUPS else ""
