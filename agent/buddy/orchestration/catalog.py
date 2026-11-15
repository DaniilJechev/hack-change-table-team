"""Closed scenario catalog. MCP permissions remain independently enforced by Hub.

Parameters stay optional, matching existing clarification flows. Internal follow-up IDs never
appear in the public tool schema. Handlers name Registry adapters, not arbitrary import paths.
"""

from dataclasses import dataclass

ROLES = frozenset({"intern", "mentor", "teamlead"})


@dataclass(frozen=True)
class Scenario:
    name: str
    handler: str
    params: frozenset[str] = frozenset()
    description: str = ""
    roles: frozenset[str] = ROLES
    routed: bool = True
    agent_tool: bool = False

    def tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        name: {"type": "string", "description": ARGUMENTS[name]}
                        for name in sorted(self.params)
                    },
                    "additionalProperties": False,
                },
            },
        }


ARGUMENTS = {
    "source": "github, jira или calendar",
    "task": "ключ задачи Jira, например ORD-118",
    "area": "область, сервис или имя человека",
    "situation": "ситуация своими словами",
    "person": "имя человека",
    "object": "объект: чат, календарь, доска, репозиторий, проект Jira",
    "query": "вопрос по playbook",
}

SCENARIOS = {
    s.name: s
    for s in (
        Scenario(
            "brief_now",
            "_brief",
            frozenset(["source"]),
            "Что нового у пользователя: PR, задачи, встречи. Без source — весь бриф, вызывай один раз.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "access_status",
            "_access_status",
            frozenset([]),
            "Доступы пользователя к GitHub, Jira, Miro, Calendar, Telegram и как их получить.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "access_task_analysis",
            "_access_task",
            frozenset(["task"]),
            (
                "Доступы для задачи Jira: что нужно, точные статусы и у кого запросить. "
                "Уже включает проверку доступов; access_status дополнительно не нужен."
            ),
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "interns_status",
            "_interns",
            frozenset([]),
            "Сводка наставника по его стажёрам, в том числе их доступы.",
            frozenset({"mentor", "teamlead"}),
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q1",
            "_team",
            frozenset(["area"]),
            "Кто отвечает за область или сервис, за что отвечает человек; без area — вся команда.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q2",
            "_team",
            frozenset(["situation"]),
            "К кому идти в ситуации: «упал staging», «вопрос по Kafka».",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q3",
            "_team",
            frozenset([]),
            "Наставник, тимлид и первый контакт пользователя.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q4",
            "_team",
            frozenset(["person"]),
            "Можно ли писать человеку напрямую и как.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q5",
            "_team",
            frozenset([]),
            "Чем занята команда: этап проекта, прогресс эпиков.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "team_q6",
            "_team",
            frozenset(["task"]),
            "Как задача Jira связана с проектом команды.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario("team_q7", "_team", frozenset([]), "Стек команды.", ROLES, routed=True, agent_tool=True),
        Scenario(
            "team_q8",
            "_team",
            frozenset(["object"]),
            "Ссылка на объект команды: чат, календарь, доска, репозиторий, проект Jira.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario(
            "playbook_qa",
            "_playbook",
            frozenset(["query"]),
            "Общие процессы и правила Авито из публичного AvitoTech Playbook.",
            ROLES,
            routed=True,
            agent_tool=True,
        ),
        Scenario("help", "_template", frozenset(["menu"]), "", ROLES, routed=True, agent_tool=False),
        Scenario("clarify", "_template", frozenset(["options"]), "", ROLES, routed=True, agent_tool=False),
        Scenario("out_of_scope", "_template", frozenset([]), "", ROLES, routed=True, agent_tool=False),
        Scenario("brief_render", "_brief", frozenset([]), "", ROLES, routed=False, agent_tool=False),
        Scenario("access_events", "_access_events", frozenset([]), "", ROLES, routed=False, agent_tool=False),
    )
}
ROUTER_CASES = tuple(s.name for s in SCENARIOS.values() if s.routed)
PARAMS = {s.name: s.params for s in SCENARIOS.values() if s.routed}
TOOLS = {s.name: s.description for s in SCENARIOS.values() if s.agent_tool}
MENTOR_ONLY = frozenset(s.name for s in SCENARIOS.values() if s.roles != ROLES)
SCHEDULED = tuple(s.name for s in SCENARIOS.values() if not s.routed)
