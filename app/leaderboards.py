"""Leaderboard queries: round-level points and cumulative tournament standings.

Both are simple sums over `Prediction.points`, which is populated once a fixture
finishes and scoring runs (see app.scoring.score_fixture).
"""

from sqlalchemy import func

from app.extensions import db
from app.models import Fixture, Prediction, Round, User


def round_leaderboard(round_: Round):
    """List of (user, round_points, tournament_points) for a single round, highest
    round_points first. Includes users with 0 in either column.

    Surfacing the tournament total alongside the round total lets users see, as results
    come in during a round, both how they're doing this round *and* where that leaves
    them overall - without a separate page lookup (see main.round_leaderboard_view /
    round_leaderboard_live, which polls this on the same cadence as live scores).
    """
    round_points = (
        db.session.query(
            Prediction.user_id.label("user_id"),
            func.coalesce(func.sum(Prediction.points), 0).label("round_points"),
        )
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .filter(Fixture.round_id == round_.id)
        .group_by(Prediction.user_id)
        .subquery()
    )
    tournament_points = (
        db.session.query(
            Prediction.user_id.label("user_id"),
            func.coalesce(func.sum(Prediction.points), 0).label("tournament_points"),
        )
        .group_by(Prediction.user_id)
        .subquery()
    )
    rows = (
        db.session.query(
            User,
            func.coalesce(round_points.c.round_points, 0).label("round_points"),
            func.coalesce(tournament_points.c.tournament_points, 0).label("tournament_points"),
        )
        .outerjoin(round_points, round_points.c.user_id == User.id)
        .outerjoin(tournament_points, tournament_points.c.user_id == User.id)
        .group_by(User.id)
        .order_by(db.desc("round_points"), db.desc("tournament_points"), User.username.asc())
        .all()
    )
    return rows


def tournament_standings():
    """List of (user, points) cumulative across every scored prediction, highest first."""
    rows = (
        db.session.query(User, func.coalesce(func.sum(Prediction.points), 0).label("points"))
        .outerjoin(Prediction, Prediction.user_id == User.id)
        .group_by(User.id)
        .order_by(db.desc("points"), User.username.asc())
        .all()
    )
    return rows
