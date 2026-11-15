"""Process-local interaction state. Cleared on restart; never a source of user identity."""

from dataclasses import dataclass, field

from buddy.contracts import Context


@dataclass
class InteractionState:
    chats: dict[str, int] = field(default_factory=dict)
    known: dict[str, Context] = field(default_factory=dict)
    awaiting: dict[int, tuple[str, str, Context]] = field(default_factory=dict)
    awaiting_card: dict[int, Context] = field(default_factory=dict)
    brief_messages: dict[tuple[int, int], str] = field(default_factory=dict)
