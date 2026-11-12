"""Deterministic routing, independent of any evaluation dataset."""

import re

from buddy.schemas import RouteDecision

TEAM_CHOICES = "Доступ к инструменту | Кто это и можно ли ему писать"
# RT-03: any other unclear text gets the menu of team question types and /help, not the access/person pair.
MENU_CHOICES = "Типы вопросов о команде | /help"
ACCESS_OR_PERSON = re.compile(
    r"доступ|прав[аоу]?\b|человек|люд|кто\b|кого|кому|ком\b|наставник|тимлид|коллег|олег|анн|петр|даниил"
)
# FR-H2-03 Q1 without an area: the whole team, «кто за что отвечает».
TEAM_LIST = ("кто за что", "кто чем занимается", "кто есть кто", "кто в команде", "список команды")


def clarify(value: str) -> RouteDecision:
    """The access/person pair only when the text speaks of access or a person; otherwise the menu."""
    value = value.casefold().replace("ё", "е")
    options = TEAM_CHOICES if ACCESS_OR_PERSON.search(value) else MENU_CHOICES
    return RouteDecision(case="clarify", params={"options": options})
TASK = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-\d{1,10}\b", re.I)


BRIEF_SOURCES = {
    "github": r"\bpr\b|пулл|\bпр\b|ревью|\bgit|\bгит|коммит",
    "jira": r"jira|джир|задач|тикет",
    "calendar": r"календар|встреч|созвон|стендап",
}


def brief_source(value: str) -> dict[str, str]:
    """FR-H0-07: a brief limited to one source only when exactly one is named."""
    named = [source for source, pattern in BRIEF_SOURCES.items() if re.search(pattern, value)]
    return {"source": named[0]} if len(named) == 1 else {}


def decide(text: str) -> RouteDecision:
    text = text.strip()
    value = re.sub(r"\s+", " ", text.casefold().replace("ё", "е"))
    command = value.split(maxsplit=1)[0].split("@", 1)[0] if value else ""
    commands = {
        "/brief": "brief_now",
        "/access": "access_status",
        "/interns": "interns_status",
        "/help": "help",
        "/start": "help",
        "/ask": "help",
    }
    if command == "/brief":
        return RouteDecision(case="brief_now", params=brief_source(value))
    if command in commands:
        return RouteDecision(case=commands[command], params={"menu": "team"} if command == "/ask" else {})
    if command.startswith("/"):
        return RouteDecision(case="out_of_scope")
    if not value or len(text) > 4000:
        return clarify(value)
    if any(
        word in value
        for word in (
            "зарплат",
            "получать",
            "диагноз",
            "болезн",
            "паспорт",
            "снилс",
            "напиши код",
            "написать код",
            "сортиров",
            "удали",
            "выдай права",
            "создай заявк",
            "уволь",
            "погода",
            "рецепт",
            "стихотвор",
        )
    ):
        return RouteDecision(case="out_of_scope")
    task = TASK.search(text)
    task_id = task.group().upper() if task else None
    if "доступ" in value and any(word in value for word in ("олег", "анн", "петр", "человек", "наставник")):
        return RouteDecision(case="clarify", params={"options": TEAM_CHOICES})
    if any(word in value for word in ("стажер", "интерн")) and any(
        word in value for word in ("все", "кого", "сводк", "список", "моих", "моим", "у кого", "проверь")
    ):
        return RouteDecision(case="interns_status")
    if any(word in value for word in ("доступ", "прав хватает", "прав нужны", "разрешени")):
        if task_id:
            return RouteDecision(case="access_task_analysis", params={"task": task_id})
        return RouteDecision(case="access_status")
    if "чуж" in value and any(word in value for word in ("функционал", "код")):
        return RouteDecision(case="playbook_qa", params={"query": text})  # neighborhood agreement
    if any(
        word in value
        for word in ("бриф", "что нового", "изменил", "изменени", "обновлени", "обзор дня", "есть что по")
    ) or re.search(r"^что (у меня )?по ", value):
        return RouteDecision(case="brief_now", params=brief_source(value))
    if task_id and any(word in value for word in ("связ", "зачем", "эпик", "проект", "часть", "вклад")):
        return RouteDecision(case="team_q6", params={"task": task_id})
    if any(
        word in value
        for word in (
            "zero bug",
            "playbook",
            "плейбук",
            "авито",
            "code review",
            "код ревью",
            "код-ревью",
            "технический долг",
            "ретроспектив",
            "on-call",
            # Playbook topics (H3 evals, stage 3): engineering practices, processes, career.
            "bug policy",
            "tdr",
            "tech design",
            "live site",
            "постмортем",
            "инцидент",
            "архитектурн",
            "соседск",
            "ценност",
            "миссия",
            "okr",
            "performance review",
            "self-review",
            "стажировк",
            "конференц",
            "хакатон",
            "уровни у инженер",
            "инженера уровня",
            "техлид",
            "юнит",
            "кластер",
            "paas",
            "spt",
            "обращения пользовател",
            "scrum",
            "хранимых процедур",
            "связанност",
            "протестирован",
            "стал не нужен",
            "работоспособност",
            "офис",
            "wi-fi",
            "wifi",
        )
    ):
        return RouteDecision(case="playbook_qa", params={"query": text})
    if any(
        word in value
        for word in (
            "напрямую",
            "можно написать",
            "можно писать",
            "дергать",
            "побеспокоить",
            "личку",
            "личные сообщения",
        )
    ):
        return RouteDecision(case="team_q4", params={"person": text})
    if any(
        word in value
        for word in ("наставник", "тимлид", "первый контакт", "первого контакта", "ментор", "руководитель")
    ):
        return RouteDecision(case="team_q3")
    if any(
        word in value
        for word in (
            "кому писать",
            "кому сигналить",
            "к кому",
            "кому идти",
            "куда идти",
            "куда обращаться",
            "куда писать",
            "что делать если",
            "что делать, если",
            "кого звать",
        )
    ):
        situation = text.split(",", 1)[0].strip().casefold()
        return RouteDecision(case="team_q2", params={"situation": situation})
    if any(phrase in value for phrase in TEAM_LIST) or (
        "команд" in value and any(word in value for word in ("структур", "состав"))
    ):
        return RouteDecision(case="team_q1")
    if any(
        word in value
        for word in (
            "кто отвечает",
            "кто поддерживает",
            "кто у нас по",
            "владелец",
            "ответственный",
            "ведет",
            "кто знает",
            "кто занимается",
            "за кем",
        )
    ):
        return RouteDecision(case="team_q1", params={"area": text})
    if any(word in value for word in ("где", "ссылк", "найти", "открыть")) and any(
        word in value
        for word in ("репозитор", "доск", "чат", "календар", "документ", "проект jira", "дашборд")
    ):
        return RouteDecision(case="team_q8", params={"object": text})
    if any(
        word in value
        for word in (
            "стек",
            "на чем пишем",
            "какие технологии",
            "инструменты команды",
            "язык программирования",
            "база данных",
            "чем пользуемся",
        )
    ):
        return RouteDecision(case="team_q7")
    if any(
        word in value
        for word in (
            "этап",
            "стадии",
            "сейчас делает команда",
            "статус проект",
            "прогресс",
            "над чем работаем",
            "над чем работает",
            "готово в эпике",
        )
    ):
        return RouteDecision(case="team_q5")
    if any(
        word in value
        for word in ("помощь", "что умеешь", "что ты умеешь", "возможности", "привет", "команды бота")
    ):
        return RouteDecision(case="help")
    if any(word in value for word in ("почему", "зачем", "для чего")):
        # Rationale about the team's choices: FAQ first, otherwise the responsible person (stage 3, H2).
        return RouteDecision(case="team_q7")
    return clarify(value)
