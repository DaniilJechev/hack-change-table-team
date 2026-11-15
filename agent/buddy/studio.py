"""Entry of `langgraph dev` (LangGraph Studio): the real request pipeline behind a chat.

Always the demo: synthetic fixtures, no live accounts, nothing written to GitHub, Jira, Calendar, Miro
or Telegram. The LLM is the one configured in `.env` (LLM_IN_DEMO=true with a key), otherwise the fake
one, and then the Router prompts are still recorded. The persona is chosen per assistant or run with
`{"configurable": {"persona": "intern" | "mentor"}}`.
"""

from buddy.config import Settings
from buddy.contracts import Context
from buddy.llm.client import create_llm
from buddy.orchestration.graph import build_graph
from buddy.orchestration.router.service import Router
from buddy.orchestration.service import AgentService

# One synthetic demo session per persona (the demo controller keys sessions by an integer).
PERSONAS = {"intern": 1, "mentor": 2}


class Chat:
    def __init__(self, service: AgentService):
        self.service = service

    def context(self, config) -> Context:
        persona = (config.get("configurable") or {}).get("persona", "intern")
        persona = persona if persona in PERSONAS else "intern"
        user = PERSONAS[persona]
        if not self.service.demo.active(user):
            self.service.demo.command(user, persona)
        return self.service.demo.context(user)

    def runtime(self, context: Context):
        return self.service.runtime(context)


def build_studio_graph(settings: Settings | None = None):
    # demo_mode is forced: Studio never gets live runtimes, whatever the .env says.
    settings = settings or Settings(demo_mode=True)
    llm = create_llm(settings)
    service = AgentService(Router(llm), demo_mode=True, settings=settings, llm=llm)
    return build_graph(service.router, service.executors, service.memory, llm, service.agent, Chat(service))


graph = build_studio_graph()
