"""H2 executors: eight team questions from the team card, FAQ and Jira, with escalation to people.

Answers are built by code from our data; every fact carries a link inserted by code (NFR-LINK-1).
"""

import re
import uuid

from buddy.access.checker import payload
from buddy.access.service import TITLES, AccessService
from buddy.data.base import DataError
from buddy.executors.context import Context
from buddy.mcp_hub.errors import MCPError
from buddy.runtime import Runtime
from buddy.schemas import Action, ExecutorResponse

NOT_FOUND = "В карточке и FAQ команды ответа нет."
FOLD_PEOPLE = 3


def person_named(people: list[dict], text: str) -> dict | None:
    """A card person named in the text, in any case form: «Олег», «олега», «Пётр», «петру»."""
    value = text.casefold().replace("ё", "е")
    for person in people:
        stem = person["name"].casefold().replace("ё", "е").rstrip("аяеиоуыюь")
        if len(stem) >= 3 and re.search(rf"\b{re.escape(stem)}", value):
            return person
    return None


def people_count(number: int) -> str:
    """«2 человека», «5 человек», «12 человек»."""
    few = number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14)
    return f"{number} человека" if few else f"{number} человек"


UNAVAILABLE = "Данные команды сейчас недоступны. Попробуйте позже."
WHY = ("почему", "зачем", "для чего")
STOP = {"почему", "зачем", "кому", "писать", "можно", "нужно", "через", "которые", "какой", "какая", "какие"}
# Words that describe any situation; a q2 route matches by its topic («вопрос по Kafka» → Kafka).
GENERIC = {"вопрос", "вопросы", "проблема", "проблемы", "ситуация"}
OBJECT_WORDS = {
    "github": ("репозитор", "github", "git", "код", "pr"),
    "jira": ("jira", "задач", "эпик"),
    "miro": ("miro", "доск"),
    "calendar": ("календар", "встреч", "стендап"),
    "telegram": ("чат", "telegram", "канал"),
}
INSTRUMENTS = {"calendar": "google_calendar"}


def words(text: str) -> list[str]:
    return [
        item for item in re.findall(r"[\w#.+-]{4,}", text.casefold().replace("ё", "е")) if item not in STOP
    ]


def rating(case: str) -> list[Action]:
    return [
        Action(label="Помогло", data=f"rate:bot:{case}:1"),
        Action(label="Не помогло", data=f"rate:bot:{case}:0"),
    ]


class TeamService:
    def __init__(self, access: AccessService):
        self.access = access
        self.pending: dict[str, dict] = {}
        self.deferred: dict[str, list[str]] = {}
        self.metrics = {"helpful": 0, "not_helpful": 0}

    @staticmethod
    def _key(context: Context) -> str:
        return AccessService.key(context)

    def _answer(self, case: str, text: str, links: list[str], extra: list[Action] | None = None, folded=None):
        return ExecutorResponse(
            result=text,
            folded=folded or [],
            links=list(dict.fromkeys(link for link in links if link)),
            actions=[*(extra or []), *rating(case)],
        )

    async def _not_found(self, context, runtime, card, question, responsible=None) -> ExecutorResponse:
        people = {person["user_id"]: person for person in card.get("people", [])}
        first = next((person for person in card.get("people", []) if person.get("first_contact")), None)
        target = people.get(responsible) if responsible else first
        self.pending[self._key(context)] = {
            "text": question,
            "responsible": target["user_id"] if target else None,
        }
        actions = []
        if target and target["user_id"] != context.user_id:
            actions.append(Action(label=f"Спросить: {target['name']}", data="esc:resp"))
        actions.append(Action(label="Спросить наставника", data="esc:mentor"))
        actions.append(Action(label="Отложить на 1:1", data="esc:defer"))
        # FR-H2-04: an expert only when the card names one for the topic; otherwise the first contact.
        text, links = NOT_FOUND, []
        if target:
            who = f"{target['name']} ({target['role']})" if target.get("role") else target["name"]
            if responsible and target is people.get(responsible):
                text = f"{NOT_FOUND} По теме лучше всего знает {who}."
            else:  # B-08: «no answer» is said once
                text = (
                    "В карточке и FAQ ответа нет."
                    f" Первый контакт в команде — {who}: он подскажет, к кому идти."
                )
            url = str(target.get("contact_url", ""))
            links = [url] if url.startswith(("https://", "http://")) else []
        return ExecutorResponse(result=text, actions=actions, links=links)

    async def _faq(self, context, runtime, question: str) -> ExecutorResponse | None:
        for word in words(question):
            try:
                found = await runtime.data.faq_search(context.user_id, word)
            except DataError:
                return None
            if found:
                item = found[0]
                if not str(item.get("source_path", "")).startswith(("https://", "http://")):
                    continue
                return ExecutorResponse(
                    result=f"Ответ из FAQ команды на вопрос «{item['question']}»:\n{item['answer']}",
                    links=[item["source_path"]] if item["source_path"].startswith("http") else [],
                    actions=rating("faq"),
                )
        return None

    async def _jira(self, context, runtime, case: str, key: str) -> dict | None:
        try:
            value = payload(
                await runtime.gateway.call(
                    case, "jira", "jira_get_issue", {"issue_key": key}, user_id=context.user_id
                )
            )
        except MCPError:
            return None
        return value if isinstance(value, dict) else None

    def _responsible(self, card, question: str) -> str | None:
        text = question.casefold()
        for person in card.get("people", []):
            if any(area.casefold() in text for area in person.get("areas", [])):
                return person["user_id"]
        return None

    async def answer(
        self, case: str, params: dict, text: str, context: Context, runtime: Runtime
    ) -> ExecutorResponse:
        response = await self._answer_query(case, params, text, context, runtime)
        return await self.record(response, text.strip() or f"Вопрос о команде ({case})", context, runtime)

    async def record(
        self,
        response: ExecutorResponse,
        question: str,
        context: Context,
        runtime: Runtime,
        capability: str = "h2",
    ) -> ExecutorResponse:
        """Store a sourced bot answer so that its [Помогло]/[Не помогло] rating has an owner."""
        rated = any((action.data or "").startswith("rate:bot:") for action in response.actions)
        if response.links and rated:  # a «not found» with a contact link has nothing to rate
            question_id = "b-" + uuid.uuid4().hex[:16]
            try:
                await runtime.data.record_bot_answer(
                    context.user_id, question_id, question, response.result, response.links, capability
                )
            except DataError:
                response.actions = [
                    action for action in response.actions if not (action.data or "").startswith("rate:bot:")
                ]
            else:
                for action in response.actions:
                    if (action.data or "").startswith("rate:bot:"):
                        mark = action.data.rsplit(":", 1)[-1]
                        action.data = f"rate:bot:{question_id}:{mark}"
        return response

    async def _answer_query(
        self, case: str, params: dict, text: str, context: Context, runtime: Runtime
    ) -> ExecutorResponse:
        try:
            card = (await runtime.data.team_card(context.user_id))["document"]
        except DataError as exc:
            if exc.kind == "not_found":
                return ExecutorResponse(
                    result="Карточка вашей команды пока не загружена. Спросите наставника."
                )
            return ExecutorResponse(result=UNAVAILABLE)
        question = text.strip() or params.get("area") or params.get("object") or ""
        # «Почему/зачем» about the team is not in the card: FAQ first, then a person. Task questions
        # (q5/q6: «зачем нужна ORD-118?») are answered from Jira instead.
        if case not in {"team_q5", "team_q6"} and any(word in question.casefold() for word in WHY):
            found = await self._faq(context, runtime, question)
            if found:
                return found
            return await self._not_found(context, runtime, card, question, self._responsible(card, question))
        handler = getattr(self, "_" + case)
        return await handler(params, question, card, context, runtime)

    async def _team_q1(self, params, question, card, context, runtime):
        area = (params.get("area") or "").casefold()
        people = card.get("people", [])
        named = person_named(people, f"{area} {question}")
        if named and not any(
            item.casefold() in area for person in people for item in person.get("areas", [])
        ):
            # B-14: «за что отвечает Олег?» — that person's areas, not the whole list or «not found».
            who = f"{named['name']} ({named['role']})"
            text = f"{who} отвечает за: {', '.join(named.get('areas', []))}."
            return self._answer("team_q1", text, [named.get("contact_url", "")])
        if not area:
            lines = [
                f"{person['name']} ({person['role']}): {', '.join(person['areas'])}" for person in people
            ]
            if len(lines) > FOLD_PEOPLE:  # FR-UI-01: a long team list is folded
                title = f"Кто за что отвечает — {people_count(len(lines))}, список ниже."
                return self._answer("team_q1", title, [card["source_url"]], folded=["\n".join(lines)])
            return self._answer("team_q1", "Кто за что отвечает:\n" + "\n".join(lines), [card["source_url"]])
        found = [
            person
            for person in people
            if any(item.casefold() in area or area in item.casefold() for item in person.get("areas", []))
        ]
        if found:
            text = "\n".join(f"За «{params['area']}» отвечает {p['name']} ({p['role']})." for p in found)
            return self._answer("team_q1", text, [p["contact_url"] for p in found])
        return await self._faq(context, runtime, question) or await self._not_found(
            context, runtime, card, question
        )

    async def _team_q2(self, params, question, card, context, runtime):
        situation = " ".join(filter(None, [params.get("situation"), question])).casefold().replace("ё", "е")
        people = {person["user_id"]: person for person in card.get("people", [])}
        for route in card.get("routing", []):
            topic = [word for word in words(route["situation"]) if word not in GENERIC]
            if route["situation"].casefold() in situation or (
                topic and all(word in situation for word in topic)
            ):
                then = people.get(route["then_id"], {}).get("name", route["then_id"])
                text = (
                    f"При ситуации «{route['situation']}» — сначала {route['go_to']}; "
                    f"если нет ответа за {route['wait_minutes']} мин — {then}."
                )
                return self._answer("team_q2", text, [route["source_url"]])
        # No route: the owner of the area named in the situation, before the generic first contact.
        # A 6-letter prefix covers Russian endings («интеграции» / «по интеграциям»).
        owners = [
            person
            for person in card.get("people", [])
            if any(word[:6] in situation for area in person.get("areas", []) for word in words(area))
        ]
        if owners:
            lines = ["Готового маршрута для этой ситуации нет."]
            for person in owners:
                line = f"По этой теме — {person['name']} ({person['role']})."
                rule = next(
                    (r for r in card.get("do_not_disturb", []) if r["person_id"] == person["user_id"]), None
                )
                if rule and not rule.get("direct_allowed", True):
                    via = people.get(rule.get("via_id"), {}).get("name", rule.get("via_id"))
                    line += f" Напрямую писать ограничено — через {via}."
                lines.append(line)
            return self._answer("team_q2", "\n".join(lines), [person["contact_url"] for person in owners])
        first = next((person for person in card.get("people", []) if person.get("first_contact")), None)
        if first:
            text = (
                "Готового маршрута для этой ситуации нет. В неожиданных ситуациях первый контакт — "
                f"{first['name']} ({first['role']})."
            )
            return self._answer("team_q2", text, [first["contact_url"]])
        return await self._not_found(context, runtime, card, question)

    async def _team_q3(self, params, question, card, context, runtime):
        try:
            profile = await runtime.data.profile(context.user_id)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE)
        people = {person["user_id"]: person for person in card.get("people", [])}
        lines, links = [], []
        mentor = people.get(profile.get("mentor_id", ""))
        if mentor:
            lines.append(f"Ваш наставник — {mentor['name']}.")
            links.append(mentor["contact_url"])
        lead = people.get(profile.get("teamlead_id", ""))
        if lead:
            lines.append(f"Тимлид — {lead['name']}.")
            links.append(lead["contact_url"])
        first = next((person for person in people.values() if person.get("first_contact")), None)
        if first:
            lines.append(f"Первый контакт в неожиданных ситуациях — {first['name']}.")
            links.append(first["contact_url"])
        if not lines:
            return await self._not_found(context, runtime, card, question)
        return self._answer("team_q3", "\n".join(lines), links)

    async def _team_q4(self, params, question, card, context, runtime):
        name = (params.get("person") or question).casefold().replace("ё", "е")
        if not name:
            return ExecutorResponse(result="Кому хотите написать? Например: «Можно написать Олегу напрямую?»")
        people = card.get("people", [])
        tokens = re.findall(r"\w+", name)

        def mentioned(person: dict) -> bool:
            # Russian declension: «Анна» → «Анне», «Олег» → «Олега»; compare by stem of the name.
            full = person["name"].casefold().replace("ё", "е")
            stem = full[: max(3, len(full) - 1)]
            return person["user_id"].casefold() in name or any(token.startswith(stem) for token in tokens)

        person = next(
            (p for p in people if mentioned(p)),
            None,
        )
        if person is None:
            return ExecutorResponse(
                result="Не знаю такого человека в команде. Уточните у наставника.",
                actions=[Action(label="Спросить наставника", data="esc:mentor")],
            )
        self.pending[self._key(context)] = {"text": question, "responsible": None}
        rule = next((r for r in card.get("do_not_disturb", []) if r["person_id"] == person["user_id"]), None)
        if rule and not rule.get("direct_allowed", True):
            via = next((p["name"] for p in people if p["user_id"] == rule.get("via_id")), rule.get("via_id"))
            text = (
                f"{person['name']}: прямое обращение ограничено — {rule['reason']}. "
                f"Контакт для согласования: {via}."
            )
        else:
            text = f"{person['name']}: прямое обращение разрешено, ограничений в правилах команды нет."
        return self._answer("team_q4", text, [person["contact_url"], card["source_url"]])

    async def _team_q5(self, params, question, card, context, runtime):
        stage = card.get("stage") or {}
        if not stage.get("epic_key"):
            return await self._not_found(context, runtime, card, question)
        epic = await self._jira(context, runtime, "team_q5", stage["epic_key"])
        if epic is None:
            return ExecutorResponse(result="Не удалось получить этап проекта из Jira. Попробуйте позже.")
        progress = epic.get("progress") or {}
        done = f": готово {progress['done']} из {progress['total']} задач" if progress else ""
        key = epic.get("key", stage["epic_key"])
        text = f"Команда работает над эпиком {key} «{epic.get('summary', '')}»{done}."
        return self._answer("team_q5", text, [epic.get("url") or stage.get("source_url")])

    async def _team_q6(self, params, question, card, context, runtime):
        task = (params.get("task") or "").upper()
        if not task:
            return ExecutorResponse(result="Напишите ключ задачи, например: «Зачем нужна ORD-118?»")
        issue = await self._jira(context, runtime, "team_q6", task)
        if issue is None:
            return ExecutorResponse(result=f"Не удалось получить {task} из Jira. Попробуйте позже.")
        epic_key = issue.get("epic_key")
        epic = await self._jira(context, runtime, "team_q6", epic_key) if epic_key else None
        text = f"{task} «{issue.get('summary', '')}»"
        if epic:
            progress = epic.get("progress") or {}
            state = f" (готово {progress['done']} из {progress['total']})" if progress else ""
            text += f" — часть эпика {epic_key} «{epic.get('summary', '')}»{state}."
        else:
            text += " не привязана к эпику. Уточните у наставника."
        return self._answer("team_q6", text, [issue.get("url"), (epic or {}).get("url")])

    async def _team_q7(self, params, question, card, context, runtime):
        tools = card.get("tools") or []
        if not tools:
            return await self._not_found(context, runtime, card, question)
        return self._answer(
            "team_q7", "Стек и инструменты команды: " + ", ".join(tools) + ".", [card["source_url"]]
        )

    async def _team_q8(self, params, question, card, context, runtime):
        text = (params.get("object") or question).casefold()
        links = {item["service"]: item["url"] for item in card.get("links", [])}
        wanted = [service for service, keys in OBJECT_WORDS.items() if any(key in text for key in keys)]
        # Live repository coordinates belong to the authorized catalog, not the demo card.
        if not wanted or wanted[0] == "github":
            try:
                configured = next(
                    (
                        obj
                        for obj in await runtime.data.access_catalog(context.user_id)
                        if obj["instrument"] == "github" and not obj.get("forbidden") and obj.get("target")
                    ),
                    None,
                )
                if configured:
                    links["github"] = configured["url"]
            except DataError:
                return ExecutorResponse(result=UNAVAILABLE)
        if not wanted:
            lines = [TITLES.get(INSTRUMENTS.get(service, service), service) for service in links]
            return self._answer("team_q8", "Ссылки команды:\n" + "\n".join(lines), list(links.values()))
        service = wanted[0]
        if service not in links:
            return await self._not_found(context, runtime, card, question)
        response = self._answer(
            "team_q8",
            f"{TITLES.get(INSTRUMENTS.get(service, service), service)} — источник указан ниже.",
            [links[service]],
        )
        try:
            catalog = await runtime.data.access_catalog(context.user_id)
            only = {
                item["id"]
                for item in catalog
                if item["instrument"] == INSTRUMENTS.get(service, service) and not item.get("forbidden")
            }
            items = await self.access._items("team_q8", context, runtime, only) if only else []
        except (DataError, MCPError):
            items = []
        for item in items:
            if item.status == "granted":
                response.result += "\nДоступ: ✅ есть"
            elif item.status in {"missing", "stale", "requested"}:
                response.result += "\nДоступа пока нет."
                if item.request_url:
                    response.actions.insert(0, Action(label="Где запросить доступ", url=item.request_url))
                if item.status == "missing" and item.request_status in {"", "error"}:
                    response.actions.insert(
                        1, Action(label="Запросить доступ", data="request:" + item.object_id)
                    )
        return response

    async def escalate(self, kind: str, context: Context, runtime: Runtime):
        """Returns (reply to the author, [(recipient_user_id, message)])."""
        pending = self.pending.get(self._key(context))
        if not pending:
            return ExecutorResponse(result="Нет вопроса для передачи. Задайте вопрос ещё раз."), []
        if kind == "defer":
            self.deferred.setdefault(self._key(context), []).append(pending["text"])
            self.pending.pop(self._key(context), None)
            return ExecutorResponse(result="Сохранил вопрос, чтобы обсудить на 1:1 с наставником."), []
        try:
            profile = await runtime.data.profile(context.user_id)
        except DataError:
            return ExecutorResponse(result=UNAVAILABLE), []
        target = pending["responsible"] if kind == "resp" else profile.get("mentor_id")
        if not target or target == context.user_id:
            return ExecutorResponse(
                result="Не нашёл, кому передать вопрос. Обратитесь к наставнику лично."
            ), []
        question_id = "q-" + uuid.uuid4().hex[:16]
        try:
            await runtime.data.create_question(context.user_id, question_id, pending["text"], target)
            card = (await runtime.data.team_card(context.user_id))["document"]
        except DataError:
            return ExecutorResponse(result="Не удалось передать вопрос. Попробуйте позже."), []
        self.pending.pop(self._key(context), None)
        names = {person["user_id"]: person["name"] for person in card.get("people", [])}
        week = profile.get("internship_week")
        about = f"{profile['display_name']}" + (f", неделя {week}" if week else "")
        note = ExecutorResponse(
            result=(
                f"📩 Вопрос от {about}:\n«{pending['text']}»\n"
                f"Текущая задача: {profile.get('current_task') or 'не указана'}.\n"
                f"Бот проверил {pending.get('checked', 'карточку команды и FAQ')} — ответа нет."
            ),
            actions=[Action(label="Ответить", data="ans:" + question_id)],
        )
        reply = ExecutorResponse(
            result=f"Передал вопрос: {names.get(target, target)}. Пришлю ответ, как только он появится."
        )
        if context.demo:
            reply.actions.append(
                Action(label=f"Ответить за: {names.get(target, target)} (демо)", data="ans:" + question_id)
            )
        return reply, [(target, note)]

    async def deliver_answer(self, answerer: str, question_id: str, text: str, runtime: Runtime):
        """Answerer replies; returns [(recipient_user_id, message)] for the author and the answerer."""
        try:
            record = await runtime.data.answer_question(answerer, question_id, text)
            profile = await runtime.data.profile(answerer)
        except DataError as exc:
            message = "Ответить может только адресат вопроса." if exc.kind == "forbidden" else UNAVAILABLE
            return [(answerer, ExecutorResponse(result=message))]
        author = ExecutorResponse(
            result=(
                f"{profile['display_name']} ответил(а) на ваш вопрос «{record['text']}»:\n" + record["answer"]
            ),
            actions=[
                Action(label="Помогло", data=f"rate:{question_id}:1"),
                Action(label="Не помогло", data=f"rate:{question_id}:0"),
            ],
        )
        own = ExecutorResponse(
            result=(
                "Ответ отправлен. Сохранить его в FAQ команды, чтобы следующие стажёры получали его без вас?"
            ),
            actions=[
                Action(label="Сохранить в FAQ", data="faq:" + question_id),
                Action(label="Не сохранять", data="nofaq:" + question_id),
            ],
        )
        return [(record["user_id"], author), (answerer, own)]

    async def publish(self, answerer: str, question_id: str, runtime: Runtime) -> ExecutorResponse:
        try:
            await runtime.data.publish_faq(answerer, "f-" + question_id, question_id)
        except DataError:
            return ExecutorResponse(result="Сохранить в FAQ может только автор ответа.")
        return ExecutorResponse(result="Сохранено в FAQ команды. Спасибо!")

    async def rate(self, user_id: str, value: str, helpful: bool, runtime: Runtime) -> ExecutorResponse:
        try:
            if value.startswith("bot:"):
                await runtime.data.rate_bot_answer(user_id, value.removeprefix("bot:"), helpful)
            else:
                await runtime.data.rate_question(user_id, value, helpful)
        except DataError:
            return ExecutorResponse(result="Не удалось сохранить оценку. Откройте вопрос заново.")
        self.metrics["helpful" if helpful else "not_helpful"] += 1
        return ExecutorResponse(result="Спасибо за оценку!")
