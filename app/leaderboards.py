"""Leaderboard queries: round-level points and cumulative tournament standings.

Both are simple sums over `Prediction.points`, which is populated once a fixture
finishes and scoring runs (see app.scoring.score_fixture).
"""

from sqlalchemy import func

from app.extensions import db
from app.models import Fixture, Prediction, Round, User


def round_leaderboard(round_: Round):
    """List of (user, points) for a single round, highest first. Includes users with 0."""
    rows = (
        db.session.query(User, func.coalesce(func.sum(Prediction.points), 0).label("points"))
        .outerjoin(
            Prediction,
            db.and_(
                Prediction.user_id == User.id,
                Prediction.fixture_id.in_(
                    db.session.query(Fixture.id).filter(Fixture.round_id == round_.id)
                ),
            ),
        )
        .group_by(User.id)
        .order_by(db.desc("points"), User.username.asc())
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
