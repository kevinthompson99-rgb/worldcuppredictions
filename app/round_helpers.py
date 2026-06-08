"""Helpers for finding rounds by their admin-curated lifecycle status (see Round.status).

At most one round is ever DRAFT and at most one is ever ACTIVE (enforced in the admin
blueprint's create/publish actions) - so "the" draft and "the" active round are
well-defined singletons from the user-facing app's point of view.
"""

from app.models import ROUND_STATUS_ACTIVE, ROUND_STATUS_COMPLETE, ROUND_STATUS_DRAFT, Round


def get_active_round():
    """The single round currently visible to users for predictions/results, if any."""
    return Round.query.filter_by(status=ROUND_STATUS_ACTIVE).first()


def get_draft_round():
    """The round the admin is currently preparing (invisible to regular users), if any."""
    return Round.query.filter_by(status=ROUND_STATUS_DRAFT).first()


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
