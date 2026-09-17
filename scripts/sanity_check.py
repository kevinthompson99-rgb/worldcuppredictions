"""Sanity-check suite: verifies data coherence across leaderboards, results and
stats in LEPREM - every check compares a value derived directly from the raw
Prediction/Fixture rows against what the app's own leaderboard/stats helpers
report, so a discrepancy here means a real (not just cosmetic) bug.

Read-only: nothing here writes to the database. Deliberately does NOT go
through app.create_app() - that also runs pending Alembic migrations, upserts
the admin user, and (whenever FOOTBALL_DATA_API_KEY is set, as it is in
production) starts the live-score polling scheduler as a side effect of just
constructing the app object. None of that belongs in a diagnostic script, so
this builds the minimal Flask + SQLAlchemy binding needed to query the models
and nothing else.

Run with:

    python scripts/sanity_check.py

Reads DATABASE_URL from the environment exactly as the app does (see
config.py) - point it at whichever database you want checked, e.g. via
`railway run --service <name> python scripts/sanity_check.py` for production.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from sqlalchemy import func

from app.extensions import db
from config import Config


def build_readonly_app():
    """The DB-only subset of app.create_app(): config + db.init_app + model
    registration. Skips migrations, admin seeding, blueprints and the
    scheduler entirely - see module docstring for why.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    from app import models  # noqa: F401  (registers model metadata with SQLAlchemy)

    return app


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

_results = []  # (number, title, "PASS"/"FAIL", [detail, ...])


def check(number, title, ok, details=None):
    details = details or []
    status = "PASS" if ok else "FAIL"
    _results.append((number, title, status, details))
    print(f"[{status}] {number}. {title}")
    for line in details:
        print(f"        {line}")
    if not details:
        print("        OK - no discrepancies found" if ok else "        (no detail)")
    print()


def info(number, title, lines):
    print(f"[INFO] {number}. {title}")
    if lines:
        for line in lines:
            print(f"        {line}")
    else:
        print("        (none)")
    print()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_season_points():
    from app.leaderboards import season_standings
    from app.models import Fixture, GAMEWEEK_STATUS_COMPLETE, Gameweek, Prediction, User

    direct = dict(
        db.session.query(Prediction.user_id, func.coalesce(func.sum(Prediction.points), 0))
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .join(Gameweek, Gameweek.id == Fixture.gameweek_id)
        .filter(Gameweek.status == GAMEWEEK_STATUS_COMPLETE)
        .group_by(Prediction.user_id)
        .all()
    )
    reported = {user.id: points for user, points in season_standings()}

    mismatches = []
    for uid in sorted(set(direct) | set(reported)):
        d, r = int(direct.get(uid, 0)), int(reported.get(uid, 0))
        if d != r:
            user = User.query.get(uid)
            name = user.display_name if user else f"user#{uid}"
            mismatches.append(f"{name} (id={uid}): direct sum={d}, season_standings()={r}")

    check(1, "Season points consistency (direct sum vs. season_standings())", not mismatches, mismatches)


def check_gameweek_points():
    from app.leaderboards import gameweek_leaderboard
    from app.models import Fixture, GAMEWEEK_STATUS_COMPLETE, Gameweek, Prediction, User

    mismatches = []
    completed = Gameweek.query.filter_by(status=GAMEWEEK_STATUS_COMPLETE).order_by(Gameweek.matchday.asc()).all()

    for gameweek in completed:
        direct = dict(
            db.session.query(Prediction.user_id, func.coalesce(func.sum(Prediction.points), 0))
            .join(Fixture, Fixture.id == Prediction.fixture_id)
            .filter(Fixture.gameweek_id == gameweek.id)
            .group_by(Prediction.user_id)
            .all()
        )
        reported = {user.id: points for user, points, _season_points in gameweek_leaderboard(gameweek)}

        for uid, d in direct.items():
            r = reported.get(uid)
            if r is None or int(d) != int(r):
                user = User.query.get(uid)
                name = user.display_name if user else f"user#{uid}"
                mismatches.append(
                    f"{gameweek.name} (id={gameweek.id}) / {name} (id={uid}): "
                    f"direct sum={int(d)}, gameweek_leaderboard()={r}"
                )

    check(
        2,
        f"Gameweek points consistency (direct sum vs. gameweek_leaderboard(), {len(completed)} COMPLETE gameweek(s))",
        not mismatches,
        mismatches,
    )


def check_stats_coherence():
    from app.blueprints.main import _stats_rows
    from app.models import Fixture, GAMEWEEK_STATUS_COMPLETE, Gameweek, Prediction
    from app.scoring import POINTS_CORRECT_RESULT, POINTS_EXACT_SCORE

    def direct_counts(points_value):
        return dict(
            db.session.query(Prediction.user_id, func.count(Prediction.id))
            .join(Fixture, Fixture.id == Prediction.fixture_id)
            .join(Gameweek, Gameweek.id == Fixture.gameweek_id)
            .filter(Gameweek.status == GAMEWEEK_STATUS_COMPLETE, Prediction.points == points_value)
            .group_by(Prediction.user_id)
            .all()
        )

    direct_exact = direct_counts(POINTS_EXACT_SCORE)
    direct_correct = direct_counts(POINTS_CORRECT_RESULT)

    mismatches = []
    for row in _stats_rows():
        uid = row["user"].id
        name = row["user"].display_name
        d_exact = direct_exact.get(uid, 0)
        d_correct = direct_correct.get(uid, 0)
        if d_exact != row["exact_scores"]:
            mismatches.append(
                f"{name} (id={uid}): direct exact_scores={d_exact}, _stats_rows()={row['exact_scores']}"
            )
        if d_correct != row["correct_results"]:
            mismatches.append(
                f"{name} (id={uid}): direct correct_results={d_correct}, _stats_rows()={row['correct_results']}"
            )

    check(3, "Stats coherence (direct exact/correct counts vs. _stats_rows())", not mismatches, mismatches)


def check_points_validity():
    from app.models import Fixture, Prediction
    from app.scoring import POINTS_CORRECT_RESULT, POINTS_EXACT_SCORE

    valid_points = {0, POINTS_CORRECT_RESULT, POINTS_EXACT_SCORE}
    problems = []

    rows = (
        db.session.query(Prediction, Fixture)
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .filter(Prediction.points.isnot(None))
        .all()
    )

    for prediction, fixture in rows:
        label = f"prediction#{prediction.id} (fixture#{fixture.id}, user#{prediction.user_id})"

        if prediction.points not in valid_points:
            problems.append(f"{label}: points={prediction.points} is not one of 0/6/16")
            continue

        if fixture.home_score is None or fixture.away_score is None:
            problems.append(f"{label}: points={prediction.points} but fixture has no score")
            continue

        exact_match = (
            prediction.predicted_home == fixture.home_score
            and prediction.predicted_away == fixture.away_score
        )
        result_match = prediction.predicted_outcome == fixture.result_outcome

        if prediction.points == POINTS_EXACT_SCORE and not exact_match:
            problems.append(
                f"{label}: points=16 but predicted {prediction.predicted_home}-{prediction.predicted_away} "
                f"!= actual {fixture.home_score}-{fixture.away_score}"
            )
        elif prediction.points == POINTS_CORRECT_RESULT and not (result_match and not exact_match):
            problems.append(
                f"{label}: points=6 but predicted_outcome={prediction.predicted_outcome} "
                f"vs result_outcome={fixture.result_outcome} (exact_match={exact_match})"
            )
        elif prediction.points == 0 and result_match:
            problems.append(
                f"{label}: points=0 but predicted_outcome={prediction.predicted_outcome} "
                f"matches result_outcome={fixture.result_outcome}"
            )

    check(4, f"Points scoring validity ({len(rows)} scored prediction(s) checked)", not problems, problems)


def check_pot_balance():
    from app.finance import gameweek_financial_summary
    from app.models import GAMEWEEK_STATUS_COMPLETE, Gameweek

    completed = Gameweek.query.filter_by(status=GAMEWEEK_STATUS_COMPLETE).order_by(Gameweek.matchday.asc()).all()
    problems = []
    unsettled = []

    for gameweek in completed:
        summary = gameweek_financial_summary(gameweek)
        if not summary["settled"]:
            unsettled.append(f"{gameweek.name} (id={gameweek.id}): COMPLETE but not settled - skipped")
            continue
        total = sum((row["financial_result"] or Decimal("0")) for row in summary["rows"])
        if total != Decimal("0.00") and total != Decimal("0"):
            problems.append(
                f"{gameweek.name} (id={gameweek.id}): financial results sum to {total}, not zero "
                f"(pot={summary['pot']}, entrants={summary['entrant_count']})"
            )

    details = problems + unsettled
    check(
        5,
        f"Pot balance coherence ({len(completed)} COMPLETE gameweek(s), {len(unsettled)} unsettled/skipped)",
        not problems,
        details,
    )


def check_manually_corrected():
    from app.models import Fixture, OUTCOME_AWAY, OUTCOME_DRAW, OUTCOME_HOME

    fixtures = Fixture.query.filter_by(manually_corrected=True).order_by(Fixture.kickoff_at.asc()).all()
    lines = []
    for fixture in fixtures:
        if fixture.result_outcome == OUTCOME_HOME:
            winner = fixture.home_team
        elif fixture.result_outcome == OUTCOME_AWAY:
            winner = fixture.away_team
        elif fixture.result_outcome == OUTCOME_DRAW:
            winner = "Draw"
        else:
            winner = "unscored"
        lines.append(
            f"fixture#{fixture.id}: {fixture.home_team} {fixture.home_score} - "
            f"{fixture.away_score} {fixture.away_team} (winner: {winner})"
        )

    info(6, f"Manually corrected fixtures ({len(fixtures)} found)", lines)


def check_orphaned_predictions():
    from app.models import Fixture, Prediction

    rows = (
        db.session.query(Prediction, Fixture)
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .filter(Fixture.gameweek_id.is_(None))
        .all()
    )
    details = [
        f"prediction#{prediction.id}: user#{prediction.user_id}, fixture#{fixture.id} "
        f"({fixture.home_team} v {fixture.away_team}) has no gameweek assigned"
        for prediction, fixture in rows
    ]
    check(7, "Orphaned predictions (fixture with no gameweek assigned)", not details, details)


def check_unscored_finished_fixtures():
    from app.models import Fixture, Prediction

    rows = (
        db.session.query(Fixture, func.count(Prediction.id))
        .join(Prediction, Prediction.fixture_id == Fixture.id)
        .filter(
            Fixture.status.in_(("FINISHED", "AWARDED")),
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Prediction.points.is_(None),
        )
        .group_by(Fixture.id)
        .all()
    )
    details = [
        f"fixture#{fixture.id} ({fixture.home_team} v {fixture.away_team}, {fixture.status}, "
        f"{fixture.home_score}-{fixture.away_score}): {count} prediction(s) still unscored"
        for fixture, count in rows
    ]
    check(8, "Unscored finished fixtures", not details, details)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = build_readonly_app()
    with app.app_context():
        db_url = app.config["SQLALCHEMY_DATABASE_URI"]
        # Never print credentials - just enough to confirm which DB got checked.
        safe_target = db_url.split("@")[-1] if "@" in db_url else db_url
        print(f"Running sanity checks against: {safe_target}\n")

        check_season_points()
        check_gameweek_points()
        check_stats_coherence()
        check_points_validity()
        check_pot_balance()
        check_manually_corrected()
        check_orphaned_predictions()
        check_unscored_finished_fixtures()

        passed = sum(1 for _, _, status, _ in _results if status == "PASS")
        failed = sum(1 for _, _, status, _ in _results if status == "FAIL")

        print("=" * 70)
        print(f"SUMMARY: {len(_results)} check(s) run - {passed} passed, {failed} failed")
        if failed:
            print()
            print("Failures:")
            for number, title, status, details in _results:
                if status != "FAIL":
                    continue
                print(f"  {number}. {title}")
                for line in details:
                    print(f"       - {line}")
        print("=" * 70)

        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
