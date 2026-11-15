from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from buddy.orchestration.catalog import ROUTER_CASES
from buddy.presentation import Action

Case = Literal[*ROUTER_CASES]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserProfile(Contract):
    user_id: str
    team_id: str
    role: Literal["intern", "mentor", "teamlead"]
    display_name: str


class RouteDecision(Contract):
    case: Case
    params: dict[str, str] = Field(default_factory=dict)


class Citation(Contract):
    source_id: str


class ScenarioResult(Contract):
    """Scenario content and sources, independent of Telegram controls."""

    result: str
    citations: list[Citation] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    folded: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(default_factory=dict)


class ExecutorResponse(ScenarioResult):
    """Backward-compatible delivery envelope around the scenario result."""

    actions: list[Action] = Field(default_factory=list)
    brief_id: str | None = None
    feedback_saved: bool = Field(default=False, exclude=True)

    def content(self) -> ScenarioResult:
        return ScenarioResult.model_validate(self.model_dump(include=set(ScenarioResult.model_fields)))


@dataclass(frozen=True)
class Context:
    user_id: str
    team_id: str
    role: Literal["intern", "mentor", "teamlead"]
    demo: bool = False
    session: int | None = None
