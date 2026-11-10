"""Deterministic routing, independent of any evaluation dataset."""

import re

from buddy.schemas import RouteDecision

TEAM_CHOICES = "Доступ к инструменту | Кто это и можно ли ему писать"
TASK = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-\d{1,10}\b", re.I)


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
    if command in commands:
        return RouteDecision(case=commands[command], params={"menu": "team"} if command == "/ask" else {})
    if command.startswith("/"):
        return RouteDecision(case="out_of_scope")
    if not value or len(text) > 4000:
        return RouteDecision(case="clarify", params={"options": TEAM_CHOICES})
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
    if any(word in value for word in ("бриф", "что нового", "изменил", "изменени", "обновлени", "обзор дня")):
        return RouteDecision(case="brief_now")
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
    return RouteDecision(case="clarify", params={"options": TEAM_CHOICES})
