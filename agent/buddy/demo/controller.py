import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
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
    persona: str = "demo-daniil"
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
        if self._bundle is None:
            self._bundle = load_bundle(self.data / "seed.json")
        actor = next(item for item in self._bundle["users"] if item["user_id"] == self.sessions[user].persona)
        return Context(
            user_id=actor["user_id"], team_id=actor["team_id"], role=actor["role"], demo=True, session=user
        )

    def command(self, user: int, action: str = "") -> str:
        if not self.enabled:
            return "Демо отключено в этом режиме."
        if action == "off":
            self.sessions.pop(user, None)
            return "Демо выключено. Реальная привязка аккаунта не менялась."
        if action not in {"", "next", "reset", "mentor", "intern"}:
            return "Команды демо: /demo, /demo next, /demo reset, /demo off."
        session = self.sessions.pop(user, None)
        if session is None or action == "reset":
            session = Session()
        if action in {"mentor", "intern"}:
            session.persona = "demo-anna" if action == "mentor" else "demo-daniil"
        at_end = action == "next" and session.step == len(self.steps) - 1
        if action == "next" and not at_end:
            session.step += 1
        self.sessions[user] = session
        while len(self.sessions) > self.capacity:
            self.sessions.popitem(last=False)
        step = session.step
        return (
            f"Демо: синтетические данные, учебный профиль {session.persona}; "
            "реальная привязка не создаётся.\n"
            f"Шаг {step + 1}/{len(self.steps)}: {self.scenario['steps'][self.steps[step]]}.\n"
            + ("Это последний шаг. /demo reset — начать сначала.\n" if at_end else "")
            + "/brief — бриф, /access — доступы, /ask — вопросы; /demo mentor и /demo intern — учебные роли. "
            "/demo next — следующий шаг; /demo off — выйти."
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
        session.data.set_mock_step(self.steps[session.step])
        return Runtime(
            data=session.data,
            gateway=hub,
            telegram=FixtureTelegram(self.data / "fixtures", self.steps[session.step]),
            # The synthetic day has a fixed clock: fixture arguments (calendar window) stay reproducible.
            brief_now=datetime.fromisoformat(self.scenario["clock"][self.steps[session.step]]),
        )

    async def replay(self, user: int, server: str, tool: str, arguments: dict) -> dict:
        return await self.hub(user).call("stage1_probe", server, tool, arguments)
