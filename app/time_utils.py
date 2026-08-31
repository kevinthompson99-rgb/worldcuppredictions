"""Shared UTC -> Europe/London conversion.

Used by both the `london` Jinja filter (app/__init__.py, for templates) and background
jobs that need the same formatting outside a template context (see app/scheduler.py,
app/blueprints/admin.py) - kept in one place so the two can't drift apart.
"""

from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")


def to_london(value, fmt="%a %d %b, %H:%M %Z"):
    """Render a naive UTC datetime (as stored in the DB) in UK local time."""
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone(LONDON).strftime(fmt)
