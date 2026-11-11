"""When the morning brief is due (FR-H0-06): 30 minutes before the first standup, else the default time."""

from datetime import datetime, time, timedelta

from buddy.brief.collectors import ABSENCE, STANDUP, moment

LEAD = timedelta(minutes=30)


def absent(events: list[dict], day: datetime) -> bool:
    """An all-day absence event (vacation, sick leave) covering the day."""
    for event in events:
        start = event.get("start")
        whole_day = isinstance(start, dict) and "date" in start and "dateTime" not in start
        if whole_day and any(word in str(event.get("summary", "")).casefold() for word in ABSENCE):
            return True
    return False


def due_at(events: list[dict], now: datetime, default: str, custom: str | None = None) -> datetime | None:
    """Send time for the local day of `now`; None on weekends and days of absence."""
    if now.weekday() >= 5 or absent(events, now):
        return None
    if custom:  # the intern's own /settings time wins over the standup rule
        hour, minute = map(int, custom.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    standups = sorted(
        at.astimezone(now.tzinfo)
        for event in events
        if any(word in str(event.get("summary", "")).casefold() for word in STANDUP)
        and str(event.get("status", "")).casefold() != "cancelled"
        and (at := moment(event.get("start"))) is not None
        and at.astimezone(now.tzinfo).date() == now.date()
    )
    if standups:
        return standups[0] - LEAD
    hour, minute = map(int, default.split(":"))
    return datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
