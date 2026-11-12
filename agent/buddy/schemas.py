from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Case = Literal[
    "brief_now",
    "access_status",
    "access_task_analysis",
    "interns_status",
    "team_q1",
    "team_q2",
    "team_q3",
    "team_q4",
    "team_q5",
    "team_q6",
    "team_q7",
    "team_q8",
    "playbook_qa",
    "help",
    "clarify",
    "out_of_scope",
]


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


class Action(Contract):
    """Inline button: either a short callback (≤ 64 bytes, Telegram limit) or an http(s) link."""

    label: str = Field(min_length=1, max_length=64)
    data: str | None = Field(default=None, max_length=64)
    url: str | None = None


class ExecutorResponse(Contract):
    result: str
    citations: list[Citation] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    # Set for a stored daily brief: the bot confirms delivery and attaches read signals to it.
    brief_id: str | None = None
    # FR-MEM-01: object IDs for the dialog memory (brief, brief_items, chunks, …); never shown to the user.
    entities: dict[str, list[str]] = Field(default_factory=dict)
    # Internal UI acknowledgment; never inferred from user-visible wording or serialized for LLM.
    feedback_saved: bool = Field(default=False, exclude=True)
