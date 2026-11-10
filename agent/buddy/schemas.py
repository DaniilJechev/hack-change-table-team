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


class ExecutorResponse(Contract):
    result: str
    citations: list[Citation] = Field(default_factory=list)
