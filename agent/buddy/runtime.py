"""Per-request dependencies of executors: our data, read-only MCP gateway, Telegram member checks."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from buddy.access.checker import Gateway, TelegramMembers
from buddy.data.base import TeamData


@dataclass
class Runtime:
    data: TeamData
    gateway: Gateway
    telegram: TelegramMembers
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
