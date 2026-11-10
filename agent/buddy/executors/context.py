from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Context:
    user_id: str
    team_id: str
    role: Literal["intern", "mentor", "teamlead"]
    demo: bool = False
    session: int | None = None
