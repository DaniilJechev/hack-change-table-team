import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from buddy.executors.core import Context
from buddy.mcp_hub.config import SERVERS, ServerConfig
from buddy.mcp_hub.fixtures import FixtureStore
from buddy.mcp_hub.hub import Hub

DATA = Path(__file__).with_name("data")


@dataclass(frozen=True)
class Session:
    step: int = 0


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

    def active(self, user: int) -> bool:
        return self.enabled and user in self.sessions

    def context(self, user: int) -> Context:
        if not self.active(user):
            raise DemoDisabled()
        return Context(user_id="demo-daniil", team_id=self.scenario["team_id"], role="intern", demo=True)

    def command(self, user: int, action: str = "") -> str:
        if not self.enabled:
            return "Демо отключено в этом режиме."
        if action == "off":
            self.sessions.pop(user, None)
            return "Демо выключено. Реальная привязка аккаунта не менялась."
        if action not in {"", "next", "reset"}:
            return "Команды демо: /demo, /demo next, /demo reset, /demo off."
        session = self.sessions.pop(user, Session())
        step = 0 if action == "reset" else session.step
        at_end = action == "next" and step == len(self.steps) - 1
        if action == "next" and not at_end:
            step += 1
        self.sessions[user] = Session(step)
        while len(self.sessions) > self.capacity:
            self.sessions.popitem(last=False)
        return (
            "Демо: синтетические данные, учебный стажёр demo-daniil; реальная привязка не создаётся.\n"
            f"Шаг {step + 1}/{len(self.steps)}: {self.scenario['steps'][self.steps[step]]}.\n"
            + ("Это последний шаг. /demo reset — начать сначала.\n" if at_end else "")
            + "/ask — типы вопросов; /demo next — следующий шаг; /demo off — выйти."
        )

    def hub(self, user: int) -> Hub:
        if not self.active(user):
            raise DemoDisabled()
        # Each operation gets an immutable step snapshot; no shared fixture cursor.
        step = self.steps[self.sessions[user].step]
        store = FixtureStore(self.data / "fixtures", step=step)
        return Hub({name: ServerConfig(name=name) for name in SERVERS}, store, demo_mode=True)

    async def replay(self, user: int, server: str, tool: str, arguments: dict) -> dict:
        return await self.hub(user).call("stage1_probe", server, tool, arguments)
