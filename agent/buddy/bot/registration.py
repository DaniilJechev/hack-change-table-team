from aiogram import F, Router
from aiogram.filters import Command

from buddy.bot.callbacks import CALLBACKS, choose
from buddy.bot.commands import ask, demo, reset, settings, stats
from buddy.bot.messages import route_text


def register(router: Router):
    from buddy.bot.teamcard import document, teamcard

    router.message.register(teamcard, Command("teamcard"))
    router.message.register(document, F.document)
    router.message.register(demo, Command("demo"))
    router.message.register(ask, Command("ask"))
    router.message.register(stats, Command("stats"))
    router.message.register(settings, Command("settings"))
    router.message.register(reset, Command("reset"))
    router.message.register(route_text, F.text)
    router.callback_query.register(
        choose, F.data.func(lambda data: bool(data) and data.startswith(CALLBACKS))
    )
