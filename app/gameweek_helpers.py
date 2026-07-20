"""Helpers for finding gameweeks by their lifecycle status (see Gameweek.status).

At most one gameweek is ever ACTIVE (enforced in the admin blueprint's publish action).
Any number of gameweeks may be DRAFT simultaneously - all 38 are auto-created from the
API's matchday field as fixtures sync in, and the admin publishes each one in turn.
"""

from app.models import GAMEWEEK_STATUS_ACTIVE, GAMEWEEK_STATUS_COMPLETE, GAMEWEEK_STATUS_DRAFT, Gameweek


def get_active_gameweek():
    """The single gameweek currently visible to users for predictions/results, if any."""
    return Gameweek.query.filter_by(status=GAMEWEEK_STATUS_ACTIVE).first()


def get_draft_gameweeks():
    """All gameweeks the admin is currently preparing (invisible to regular users)."""
    return Gameweek.query.filter_by(status=GAMEWEEK_STATUS_DRAFT).order_by(Gameweek.matchday.asc()).all()


def get_draft_gameweek():
    """The earliest draft gameweek, if any. Kept for callers that only care about one."""
    return Gameweek.query.filter_by(status=GAMEWEEK_STATUS_DRAFT).order_by(Gameweek.matchday.asc()).first()


def get_gameweek_for_leaderboard():
    """The gameweek whose leaderboard/results are currently most relevant to users.

    Normally the live ACTIVE gameweek; between gameweeks (active just archived, next not
    yet published) falls back to the most recently completed one so the leaderboard
    doesn't just go blank.
    """
    active = get_active_gameweek()
    if active is not None:
        return active
    return (
        Gameweek.query.filter_by(status=GAMEWEEK_STATUS_COMPLETE)
        .order_by(Gameweek.matchday.desc())
        .first()
    )
