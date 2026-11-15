"""Transport-facing actions, separate from scenario response data."""

from pydantic import BaseModel, ConfigDict, Field


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Inline button: either a short callback (≤ 64 bytes, Telegram limit) or an http(s) link."""

    label: str = Field(min_length=1, max_length=64)
    data: str | None = Field(default=None, max_length=64)
    url: str | None = None
