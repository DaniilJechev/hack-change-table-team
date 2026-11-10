import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from buddy.access.checker import FixtureTelegram
from buddy.data.seed import SeedData, load_bundle
from buddy.executors.context import Context
from buddy.mcp_hub.config import SERVERS, ServerConfig
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.hub import Hub
from buddy.runtime import Runtime

DATA = Path(__file__).with_name("data")


@dataclass
class Session:
    step: int = 0
    data: SeedData | None = field(default=None)


class DemoDisabled(Exception):
    pass


class DemoController:
    def __init__(self, enabled: bool, *, data: Path = DATA, capacity: int = 256):
        if capacity < 1:
            raise ValueError("Positive session capacity required")
        self.enabled, self.data, self.capacity = enabled, data, capacity
        self.scenario = json.loads((data / "scenario.json").read_text(encoding="utf-8"))
        self.steps = tuple(self.scenario["steps"])
        self.sessions: OrderedDict[int, Session] = OrderedDict()
        self._bundle = None

    def active(self, user: int) -> bool:
        return self.enabled and user in self.sessions

    def context(self, user: int) -> Context:
        if not self.active(user):
            raise DemoDisabled()
        return Context(
            user_id="demo-daniil", team_id=self.scenario["team_id"], role="intern", demo=True, session=user
        )

    def command(self, user: int, action: str = "") -> str:
        if not self.enabled:
            return "Демо отключено в этом режиме."
        if action == "off":
            self.sessions.pop(user, None)
            return "Демо выключено. Реальная привязка аккаунта не менялась."
        if action not in {"", "next", "reset"}:
            return "Команды демо: /demo, /demo next, /demo reset, /demo off."
        session = self.sessions.pop(user, None)
        if session is None or action == "reset":
            session = Session()
        at_end = action == "next" and session.step == len(self.steps) - 1
        if action == "next" and not at_end:
            session.step += 1
        self.sessions[user] = session
        while len(self.sessions) > self.capacity:
            self.sessions.popitem(last=False)
        step = session.step
        return (
            "Демо: синтетические данные, учебный стажёр demo-daniil; реальная привязка не создаётся.\n"
            f"Шаг {step + 1}/{len(self.steps)}: {self.scenario['steps'][self.steps[step]]}.\n"
            + ("Это последний шаг. /demo reset — начать сначала.\n" if at_end else "")
            + "/access — доступы, /ask — вопросы о команде; /demo next — следующий шаг; /demo off — выйти."
        )

    def hub(self, user: int) -> Hub:
        if not self.active(user):
            raise DemoDisabled()
        # Each operation gets an immutable step snapshot; no shared fixture cursor.
        step = self.steps[self.sessions[user].step]
        store = FixtureStore(self.data / "fixtures", step=step)
        return Hub({name: ServerConfig(name=name) for name in SERVERS}, store, demo_mode=True)

    def runtime(self, user: int) -> Runtime:
        """Synthetic data per demo session; external tools only from fixtures of the current step."""
        hub = self.hub(user)
        session = self.sessions[user]
        if session.data is None:
            if self._bundle is None:
                self._bundle = load_bundle(self.data / "seed.json")
            session.data = SeedData(self._bundle)
        return Runtime(
            data=session.data,
            gateway=hub,
            telegram=FixtureTelegram(self.data / "fixtures", self.steps[session.step]),
        )

    async def replay(self, user: int, server: str, tool: str, arguments: dict) -> dict:
        return await self.hub(user).call("stage1_probe", server, tool, arguments)
