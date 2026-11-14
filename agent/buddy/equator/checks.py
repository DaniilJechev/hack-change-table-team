"""FR-H4-02/03: mandatory checks by rules only. Synthetic structure, not an Avito template."""

import re
from dataclasses import dataclass, field

from buddy.brief.models import valid_url
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
    unchecked: bool = False


def section_of(title: str) -> str | None:
    heading = normalize(title)
    best = None
    for section_id, _, synonyms in SECTIONS:
        for synonym in synonyms:
            fits = heading == synonym or (heading.startswith(synonym) and not heading[len(synonym)].isalnum())
            if fits and (best is None or len(synonym) > len(best[1])):
                best = (section_id, synonym)
    return best[0] if best else None


def title_markers(slide: Slide, profile: dict | None = None, card: dict | None = None) -> list[str]:
    text = normalize(slide.text)
    profile, card = profile or {}, card or {}
    people = {p["user_id"]: p for p in card.get("people", [])}
    name = profile.get("display_name", "")
    mentor = people.get(profile.get("mentor_id"), {}).get("name", "")
    team = card.get("name", "")

    def contains(value, *, team=False):
        words = re.findall(r"[а-яa-z]{3,}", normalize(value))
        words = [w for w in words if w not in {"учебная", "команда", "демо"}]
        patterns = []
        for word in words:
            if team and re.fullmatch(r"[а-я]+", word):
                base = re.sub(r"(?:ов|ами|ах|ам|ы)$", "", word)
                patterns.append(re.escape(base) + r"(?:ов|ами|ах|ам|ы)?")
            else:
                patterns.append(re.escape(word))
        return bool(patterns) and all(re.search(r"(?<!\w)" + p + r"(?!\w)", text) for p in patterns)

    period = len(DATE.findall(text)) >= 2 or (
        len(MONTH.findall(text)) >= 2 and bool(re.search(r"\b20\d{2}\b", text))
    )
    return [
        label
        for label, found in (
            ("имя стажёра", contains(name)),
            ("команда", contains(team, team=True)),
            ("наставник", contains(mentor)),
            ("период", period),
        )
        if not found
    ]


def structure(slides: list[Slide], profile: dict | None = None, card: dict | None = None) -> list[Section]:
    sections = {sid: Section(sid, title, synonyms) for sid, title, synonyms in SECTIONS}
    if slides:
        missing = title_markers(slides[0], profile, card)
        if not missing:
            sections["title"].slides.append(1)
        else:
            sections["title"].missing = missing
            sections["title"].unchecked = not profile or not card
    for slide in slides:
        found = section_of(slide.title)
        # Repeated title placeholders are not section contents.
        body = [line for line in slide.lines if normalize(line) != normalize(slide.title)]
        if found and body:
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
    completed_section: bool = False


def mentions(slides: list[Slide]) -> list[Mention]:
    found = []
    for slide in slides:
        completed = section_of(slide.title) == "done"
        for line in slide.lines:
            for segment in line.split(";"):
                hits = list(KEY.finditer(segment))
                for index, hit in enumerate(hits):
                    end = hits[index + 1].start() if index + 1 < len(hits) else len(segment)
                    fragment = segment[hit.end() : end]
                    prefix = segment[: hit.start()] if index == 0 else ""
                    assertion = prefix + " " + fragment
                    status = said_status(assertion)
                    groups = {group for pattern, group in WORDS if pattern.search(normalize(assertion))}
                    if len(groups) > 1 or (status is None and re.search(r"статус\s*[:—-]", assertion, re.I)):
                        status = "ambiguous"
                    if status is None and completed:
                        status = "done"
                    found.append(Mention(hit.group(), slide.number, status, completed))
    return found


@dataclass
class Fact:
    key: str
    slides: list[int]
    outcome: str  # ok, mismatch, missing, unchecked
    said: str | None = None
    status: str = ""
    url: str = ""
    completed_section: bool = False


def judge(key: str, items: list[Mention], issue: dict | None, error: str = "") -> Fact:
    """issue=None with error 'missing' or 'unchecked'; otherwise compare claimed groups."""
    slides = sorted({item.slide for item in items})
    if issue is None:
        return Fact(key, slides, error or "unchecked")
    status = issue.get("status")
    status = status.get("name", "") if isinstance(status, dict) else str(status or "")
    url = issue.get("url") if isinstance(issue.get("url"), str) else ""
    actual = jira_group(status)
    completed = any(item.completed_section for item in items)
    if issue.get("key") != key or not status or not valid_url(url):
        return Fact(key, slides, "unchecked", completed_section=completed)
    if any(i.said == "ambiguous" for i in items) or len({i.said for i in items if i.said}) > 1:
        return Fact(key, slides, "unchecked", status=status, url=url, completed_section=completed)
    for item in items:
        if item.said and actual is None:
            return Fact(key, slides, "unchecked", status=status, url=url, completed_section=completed)
        if item.said and item.said != actual:
            return Fact(key, slides, "mismatch", item.said, status, url, completed)
    return Fact(key, slides, "ok", next((i.said for i in items if i.said), None), status, url, completed)


def group_label(group: str | None) -> str:
    return GROUPS[group][0].lower() if group in GROUPS else ""
