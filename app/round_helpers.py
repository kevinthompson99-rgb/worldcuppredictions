"""Helpers for finding rounds by their admin-curated lifecycle status (see Round.status).

At most one round is ever ACTIVE (enforced in the admin blueprint's publish action).
Up to two rounds may be DRAFT simultaneously - the admin can prepare the next two
rounds while the current one is still playing out.
"""

from app.models import ROUND_STATUS_ACTIVE, ROUND_STATUS_COMPLETE, ROUND_STATUS_DRAFT, Round

MAX_DRAFT_ROUNDS = 2


def get_active_round():
    """The single round currently visible to users for predictions/results, if any."""
    return Round.query.filter_by(status=ROUND_STATUS_ACTIVE).first()


def get_draft_rounds():
    """All rounds the admin is currently preparing (invisible to regular users), up to MAX_DRAFT_ROUNDS."""
    return Round.query.filter_by(status=ROUND_STATUS_DRAFT).order_by(Round.sequence.asc()).all()


def get_draft_round():
    """The earliest draft round, if any. Kept for callers that only care about one."""
    return Round.query.filter_by(status=ROUND_STATUS_DRAFT).order_by(Round.sequence.asc()).first()


def get_round_for_leaderboard():
    """The round whose leaderboard/results are currently most relevant to users.

    Normally the live ACTIVE round; between rounds (active just archived, next not yet
    published) falls back to the most recently completed one so the leaderboard doesn't
    just go blank.
    """
    active = get_active_round()
    if active is not None:
        return active
    return (
        Round.query.filter_by(status=ROUND_STATUS_COMPLETE)
        .order_by(Round.sequence.desc())
        .first()
    )
