"""FR-AG-03..09: agent loop over the Executors.

Tools are the Router cases, executed by `Registry.execute` with the same MCP whitelists. The model
sees tool results with source labels `S1, S2…` and marks facts with `[S#]`; code checks the answer
and turns labels into links. Any doubt returns the first tool result or hands over to the Router.
"""

import asyncio
import json
import logging
import re

from buddy.contracts import Context, ExecutorResponse, RouteDecision
from buddy.data.contracts import DataError
from buddy.orchestration.catalog import SCENARIOS, TOOLS
from buddy.orchestration.router.rules import TASK, decide
from buddy.orchestration.router.service import PARAMS, normalize
from buddy.prompts.agent import SYSTEM
from buddy.prompts.commands import COMMANDS, referenced_command
from buddy.rag.qa import NOTE as PLAYBOOK_NOTE

# Case of a turn the agent answered without tools («привет», general onboarding advice).
CHAT = "chat"
MAX_STEPS, MAX_CALLS, TIMEOUT = 3, 4, 20.0
MAX_ANSWER, MAX_ARGUMENT = 3000, 1000
TRUSTED = "Доверенный контекст (JSON): "

log = logging.getLogger("buddy.agent.loop")  # stable name consumed by evals/run_agent.py
MARK = re.compile(r"\[S(\d+)\]")
# A grouped label «[S1, S2]» (eval 12.11, Haiku) is split into «[S1][S2]» before the checks.
GROUP = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)+)\]")
RANGE = re.compile(r"\[S(\d+)\s*[-–—]\s*S?(\d+)\]")
# Link numbers like [1] are made by code only (render); the model writing them would fake citations.
NUMBER = re.compile(r"[ \t]*\[\d+\]")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
# FR-AG-05: a name from the team card or an access status without a tool this turn — one retry with a
# required tool of that kind.
TEAM_TOOLS = frozenset(name for name in TOOLS if name.startswith("team_q"))
ACCESS_TOOLS = frozenset({"access_status", "access_task_analysis", "interns_status"})
VERBATIM_TOOLS = ACCESS_TOOLS | {"team_q4"}
RETRY = (
    "В этом ходу ты не вызвал инструменты, а ответ называет людей команды или статус доступов. Вызови "
    "инструмент и ответь по его результату с метками источников; прошлые ответы из истории не подходят."
)
ENDING = re.compile(r"[аяйь]$")
# An access status is a sentence with an instrument, «доступ» and a status: «доступа к Miro нет».
INSTRUMENT = re.compile(r"github|гитхаб|jira|джир|miro|миро|calendar|календар|telegram|телеграм", re.I)
STATUS = re.compile(
    r"\b(?:есть|нет|выдан\w*|выдал\w*|получен\w*|открыт\w*|закрыт\w*|подтвержд\w*|запрещ\w*"
    r"|заявк\w*|рассмотр\w*|ожида\w*)\b|[✅⬜⛔⏳⏰❌]",
    re.I,
)
PR = re.compile(r"(?:\bPR|\bпр|пулл?\s*реквест\w*)\s*№?\s*#?\s*(\d{1,7})\b|#(\d{1,7})\b", re.IGNORECASE)
GRANT = re.compile(
    r"(?:^|[.!?\n]\s*)(?:пожалуйста[, ]+)?"
    r"(?:(?:выдай(?:те)?|предоставь(?:те)?)\s+(?:(?:мне|нам|ему|ей)\s+)?(?:доступ\w*|прав[ао])\b"
    r"|назначь(?:те)?\s+(?:(?:меня|его|её|ее|их)\s+)?админ\w*\b)",
    re.IGNORECASE,
)
ADVICE = re.compile(
    r"подготов\w*.{0,40}ревью|застр\w*.{0,60}задач|не могу (?:сдвинуться|продвинуться)|нет прогресса",
    re.IGNORECASE,
)


class Rejected(Exception):
    """The model's step or answer broke a rule: the turn falls back without the LLM."""


def tools_for(role: str) -> list[dict]:
    """FR-AG-03: the tool set is chosen by code from the trusted role."""
    return [s.tool() for s in SCENARIOS.values() if s.agent_tool and role in s.roles]


def checked_call(call: dict, allowed: set[str]) -> RouteDecision:
    """FR-AG-03: a known tool of this role with Router parameters only; identity never comes from here."""
    name = call.get("name")
    if name not in allowed:
        raise Rejected("tool")
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except (TypeError, ValueError) as error:
        raise Rejected("arguments") from error
    if not isinstance(arguments, dict) or not set(arguments) <= PARAMS[name]:
        raise Rejected("arguments")
    if not all(isinstance(value, str) and len(value) <= MAX_ARGUMENT for value in arguments.values()):
        raise Rejected("arguments")
    return normalize(
        RouteDecision(case=name, params={k: v.strip() for k, v in arguments.items() if v.strip()})
    )


def mentioned_prs(text: str) -> set[str]:
    return {match.group(1) or match.group(2) for match in PR.finditer(text)}


def verify(answer: str, sources: list[str], observed: str) -> str:
    """FR-AG-09: reject URLs, unknown labels, invented task keys and PRs, facts without labels."""
    answer = NUMBER.sub("", answer)

    def expand(match):
        start, end = map(int, match.groups())
        if not 1 <= start <= end <= len(sources):
            raise Rejected("label")
        return "".join(f"[S{number}]" for number in range(start, end + 1))

    answer = RANGE.sub(expand, answer)
    answer = GROUP.sub(lambda m: "".join(f"[{label.strip()}]" for label in m.group(1).split(",")), answer)
    if re.search(r"\[S(?:\d|\?|\])", MARK.sub("", answer)):
        raise Rejected("label")
    answer = answer.strip()
    if not answer or len(answer) > MAX_ANSWER or URL.search(answer):
        raise Rejected("answer")
    labels = [int(number) for number in MARK.findall(answer)]
    if any(not 1 <= label <= len(sources) for label in labels):
        raise Rejected("label")
    if sources and not labels:
        raise Rejected("unlabelled")
    known = observed.upper()
    if any(match.group(0).upper() not in known for match in TASK.finditer(answer)):
        raise Rejected("task")
    if not mentioned_prs(answer) <= mentioned_prs(observed) | set(re.findall(r"/pull/(\d+)", observed)):
        raise Rejected("pr")
    return answer


def mentions(text: str, names: list[str]) -> bool:
    """FR-AG-09: a card name in any case form: «Анна», «Анне», «Олегом»; «Аннотация» is not a name."""
    text = text.replace("ё", "е")
    for name in names:
        name = name.replace("ё", "е")
        stem = ENDING.sub("", name)
        stem = stem if len(stem) >= 3 else name
        if re.search(rf"\b{re.escape(stem)}\w{{0,3}}\b", text):
            return True
    return False


def access_claim(text: str) -> bool:
    """FR-AG-09: «у тебя есть доступ к Jira» is a fact; «могу проверить доступы к Jira» is not."""
    return any(
        "доступ" in part.casefold() and INSTRUMENT.search(part) and STATUS.search(part)
        for part in re.split(r"[.!?\n]", text)
    )


async def people_names(context: Context, runtime) -> list[str]:
    """Names of the team card except the user's own; the card unreadable — no name check."""
    if runtime is None:
        return []
    try:
        card = (await runtime.data.team_card(context.user_id))["document"]
        people = card.get("people", [])
    except (DataError, KeyError, TypeError, AttributeError):
        return []
    return [p["name"] for p in people if p.get("name") and p.get("user_id") != context.user_id]


def render(answer: str, sources: list[str]) -> tuple[str, list[str]]:
    """FR-AG-04: labels become link numbers in the order of first citation; links come from code."""
    order: list[int] = []
    for number in MARK.findall(answer):
        if int(number) not in order:
            order.append(int(number))
    text = MARK.sub(lambda match: f"[{order.index(int(match.group(1))) + 1}]", answer)
    return text, [sources[label - 1] for label in order]


class ToolCallingAgent:
    def __init__(self, llm, registry, *, timeout: float = TIMEOUT):
        self.llm, self.registry, self.timeout = llm, registry, timeout

    async def answer(
        self, text: str, context: Context, runtime=None, history: list[dict] | None = None
    ) -> tuple[RouteDecision, ExecutorResponse] | None:
        """None means no tool ran and the model failed: the caller routes the text with the Router."""
        command_help = referenced_command(text)
        if GRANT.search(text.strip()):
            return RouteDecision.model_construct(case=CHAT, params={}), ExecutorResponse(
                result="Я не выдаю доступы и не назначаю роли администратора. "
                "Могу проверить текущие доступы и подсказать, как подать заявку."
            )
        tools = tools_for(context.role)
        allowed = {tool["function"]["name"] for tool in tools}
        advice = bool(
            ADVICE.search(text) and not (TASK.search(text) or mentioned_prs(text) or INSTRUMENT.search(text))
        )
        contact = bool(
            history and re.search(r"писать|написать", text, re.I) and re.search(r"личк|напрямую", text, re.I)
        )
        lookup = not advice and not contact and decide(text).case == "playbook_qa"
        if command_help:
            advice, contact, lookup = False, False, False
        if advice or command_help:
            allowed = set()
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "system",
                "content": TRUSTED + json.dumps({"role": context.role}),
            },
            *(history or []),
            {"role": "user", "content": text},
        ]
        sources: list[str] = []
        done: list[tuple[RouteDecision, ExecutorResponse]] = []
        observed = [text]
        active, choice, retried = tools, {}, False
        if advice or command_help:
            choice = {"tool_choice": "none"}
        elif contact:
            active = [tool for tool in tools if tool["function"]["name"] == "team_q4"]
            allowed = {"team_q4"}
            choice = {"tool_choice": "required"}
        elif lookup:
            active = [tool for tool in tools if tool["function"]["name"] == "playbook_qa"]
            choice = {"tool_choice": "required"}
        try:
            async with asyncio.timeout(self.timeout):
                for _ in range(MAX_STEPS):
                    reply = await self.llm.chat(messages, active, **choice)
                    active, choice = tools, {}
                    calls = reply["tool_calls"]
                    if not calls:
                        if lookup and not any(decision.case == "playbook_qa" for decision, _ in done):
                            raise Rejected("playbook")
                        if len(done) == 1 and done[0][0].case == "playbook_qa":
                            return self._compose("", sources, done)
                        if any(decision.case in VERBATIM_TOOLS for decision, _ in done):
                            return self._compose("", sources, done)
                        # FR-AG-09: with no tool this turn, a task key or PR (even from the question
                        # or history) is a fact without a source.
                        if (
                            not done
                            and not retried
                            and mentioned_prs(text)
                            and mentioned_prs(reply["content"])
                        ):
                            retried = True
                            messages += [
                                {"role": "assistant", "content": reply["content"]},
                                {
                                    "role": "user",
                                    "content": (
                                        "Проверь PR через brief_now в этом ходу и ответь по свежим данным "
                                        "с метками источников."
                                    ),
                                },
                            ]
                            active = [tool for tool in tools if tool["function"]["name"] == "brief_now"]
                            choice = {"tool_choice": "required"}
                            continue
                        answer = verify(reply["content"], sources, "\n".join(observed) if done else "")
                        wanted = frozenset()
                        if (
                            not done
                            and not command_help
                            and mentions(answer, await people_names(context, runtime))
                        ):
                            wanted |= TEAM_TOOLS
                        if not done and access_claim(answer):
                            wanted |= ACCESS_TOOLS
                        if wanted:
                            # FR-AG-05: a person or an access status from history — refresh by a tool, once.
                            if retried:
                                raise Rejected("stale")
                            retried = True
                            kinds = (("team", TEAM_TOOLS), ("access", ACCESS_TOOLS))
                            log.info("agent retry: %s", ",".join(k for k, group in kinds if group <= wanted))
                            messages += [
                                {"role": "assistant", "content": reply["content"]},
                                {"role": "user", "content": RETRY},
                            ]
                            active = [tool for tool in tools if tool["function"]["name"] in wanted]
                            choice = {"tool_choice": "required"}
                            continue
                        return self._compose(answer, sources, done)
                    if len(done) + len(calls) > MAX_CALLS:
                        raise Rejected("budget")
                    messages.append(
                        {
                            "role": "assistant",
                            "content": reply["content"] or None,
                            "tool_calls": [
                                {
                                    "id": call["id"],
                                    "type": "function",
                                    "function": {"name": call["name"], "arguments": call["arguments"]},
                                }
                                for call in calls
                            ],
                        }
                    )
                    decisions = [checked_call(call, allowed) for call in calls]
                    if lookup and not done and not any(d.case == "playbook_qa" for d in decisions):
                        raise Rejected("playbook")
                    # One step's tools run together: a brief with its LLM wording takes ~4 s per call.
                    responses = await asyncio.gather(
                        *(self.registry.execute(d.case, d.params, context, runtime, text) for d in decisions)
                    )
                    for call, decision, response in zip(calls, decisions, responses, strict=True):
                        done.append((decision, response))
                        observation = self._observe(response, sources)
                        observed.append(observation)
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": observation})
                raise Rejected("steps")
        except Exception as error:
            # FR-AG-05: model text and errors are never shown; CancelledError still propagates.
            # Only the rule name or exception type is logged: no user text, no model output.
            reason = error.args[0] if isinstance(error, Rejected) else type(error).__name__
            log.info("agent fallback: %s", reason)
            if command_help:
                return RouteDecision(case="help"), ExecutorResponse(
                    result=f"/{command_help}: {COMMANDS[command_help]}"
                )
            if lookup and not done:
                decision = RouteDecision(case="playbook_qa", params={"query": text})
                response = await self.registry.execute("playbook_qa", decision.params, context, runtime, text)
                return self._compose("", [], [(decision, response)])
            if advice:
                return RouteDecision.model_construct(case=CHAT, params={}), ExecutorResponse(
                    result="Разбери, что мешает сдвинуться с задачей: запиши блокер и что уже пробовал. "
                    "Попроси наставника помочь с конкретным вопросом. Для ревью проверь изменения "
                    "и тесты, подготовь описание и вопросы."
                )
            return done[0] if done else None

    @staticmethod
    def _observe(response: ExecutorResponse, sources: list[str]) -> str:
        """FR-AG-04: the tool result as JSON data; source labels are global for the turn."""
        labels = []
        for link in response.links:
            if link not in sources:
                sources.append(link)
            labels.append({"id": f"S{sources.index(link) + 1}", "url": link})
        return json.dumps(
            {
                "result": re.sub(
                    r"\[(\d+)\]",
                    lambda m: (
                        f"[{labels[int(m.group(1)) - 1]['id']}]"
                        if 1 <= int(m.group(1)) <= len(labels)
                        else ""
                    ),
                    "\n".join([response.result, *response.folded]),
                ),
                "sources": labels,
                "buttons": [action.label for action in response.actions],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _compose(answer: str, sources: list[str], done) -> tuple[RouteDecision, ExecutorResponse]:
        # A single brief already owns its code-made numbering and source labels.
        if len(done) == 1 and done[0][0].case == "brief_now":
            return done[0]
        if len(done) == 1 and done[0][0].case == "playbook_qa":
            decision, response = done[0]
            if PLAYBOOK_NOTE not in response.result:
                response = response.model_copy(update={"result": response.result + "\n" + PLAYBOOK_NOTE})
            return decision, response
        if any(decision.case in VERBATIM_TOOLS for decision, _ in done):
            # Preserve checked states and named contact permissions instead of rewriting them.
            sources = []
            blocks = []
            for decision, response in done:
                block = json.loads(Agent._observe(response, sources))["result"]
                if decision.case == "team_q4" and response.links and not MARK.search(block):
                    # team_q4's first source is the requested person's card.
                    block += f" [S{sources.index(response.links[0]) + 1}]"
                blocks.append(block)
            answer = "\n\n".join(blocks)
        text, links = render(answer, sources)
        if any(decision.case == "playbook_qa" for decision, _ in done):
            if PLAYBOOK_NOTE not in text:
                text += "\n" + PLAYBOOK_NOTE
        actions, entities, brief_id = {}, {}, None
        for _, response in done:
            for action in response.actions:
                actions.setdefault(action.label, action)
            for name, values in response.entities.items():
                entities.setdefault(name, [])
                entities[name] += [value for value in values if value not in entities[name]]
            brief_id = brief_id or response.brief_id
        decision = done[0][0] if done else RouteDecision.model_construct(case=CHAT, params={})
        response = ExecutorResponse(
            result=text, links=links, actions=list(actions.values()), entities=entities, brief_id=brief_id
        )
        return decision, response


Agent = ToolCallingAgent  # compatibility for callers using the original public name
