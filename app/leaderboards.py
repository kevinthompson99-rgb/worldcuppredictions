"""Leaderboard queries: gameweek-level points and cumulative season standings.

Both are simple sums over `Prediction.points`, which is populated once a fixture
finishes and scoring runs (see app.scoring.score_fixture).
"""

from sqlalchemy import func

from app.extensions import db
from app.models import Fixture, Gameweek, Prediction, User


def gameweek_leaderboard(gameweek: Gameweek):
    """List of (user, gameweek_points, season_points) for a single gameweek, highest
    gameweek_points first. Includes users with 0 in either column.

    Surfacing the season total alongside the gameweek total lets users see, as results
    come in during a gameweek, both how they're doing this gameweek *and* where that
    leaves them overall - without a separate page lookup (see main.leaderboard, and
    app.finance.gameweek_financial_summary which layers the pot settlement on top).
    """
    gameweek_points = (
        db.session.query(
            Prediction.user_id.label("user_id"),
            func.coalesce(func.sum(Prediction.points), 0).label("gameweek_points"),
        )
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .filter(Fixture.gameweek_id == gameweek.id)
        .group_by(Prediction.user_id)
        .subquery()
    )
    season_points = (
        db.session.query(
            Prediction.user_id.label("user_id"),
            func.coalesce(func.sum(Prediction.points), 0).label("season_points"),
        )
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .join(Gameweek, Gameweek.id == Fixture.gameweek_id)
        .filter(Gameweek.status == "COMPLETE")
        .group_by(Prediction.user_id)
        .subquery()
    )
    rows = (
        db.session.query(
            User,
            func.coalesce(gameweek_points.c.gameweek_points, 0).label("gameweek_points"),
            func.coalesce(season_points.c.season_points, 0).label("season_points"),
        )
        .outerjoin(gameweek_points, gameweek_points.c.user_id == User.id)
        .outerjoin(season_points, season_points.c.user_id == User.id)
        .order_by(db.desc("gameweek_points"), db.desc("season_points"), User.display_name.asc())
        .all()
    )
    return rows


def season_standings():
    """List of (user, points) cumulative across COMPLETE gameweeks, highest first."""
    pts = (
        db.session.query(
            Prediction.user_id.label("user_id"),
            func.coalesce(func.sum(Prediction.points), 0).label("points"),
        )
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .join(Gameweek, Gameweek.id == Fixture.gameweek_id)
        .filter(Gameweek.status == "COMPLETE")
        .group_by(Prediction.user_id)
        .subquery()
    )
    rows = (
        db.session.query(User, func.coalesce(pts.c.points, 0).label("points"))
        .outerjoin(pts, pts.c.user_id == User.id)
        .order_by(db.desc("points"), User.display_name.asc())
        .all()
    )
    return rows
