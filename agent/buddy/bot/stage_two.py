"""Compatibility facade. New code imports bot modules by responsibility."""
from buddy.bot.callbacks import (
    CALLBACKS,  # noqa: F401
    choose,  # noqa: F401
)
from buddy.bot.commands import ask, demo, reset, settings, stats  # noqa: F401
from buddy.bot.messages import route_text  # noqa: F401
from buddy.bot.registration import register  # noqa: F401
from buddy.bot.rendering import (  # noqa: F401
    deliver,
    hide_brief_feedback,
    keyboard,
    prefix,
    response_menu,
    rich,
    send,
    team_menu,
)
from buddy.bot.session import context, private  # noqa: F401
