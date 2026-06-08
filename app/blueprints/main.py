from flask import Blueprint, abort, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.leaderboards import round_leaderboard, tournament_standings
from app.models import ROUND_STATUS_DRAFT, Prediction, Round, User
from app.round_helpers import get_active_round, get_round_for_leaderboard

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("main/index.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    active_round = get_active_round()

    my_prediction_count = None
    if active_round is not None:
        my_prediction_count = current_user.predictions.filter(
            Prediction.fixture.has(round_id=active_round.id)
        ).count()

    return render_template(
        "main/dashboard.html",
        active_round=active_round,
        my_prediction_count=my_prediction_count,
    )


@bp.route("/leaderboard/round")
@login_required
def round_leaderboard_view():
    round_ = get_round_for_leaderboard()
    rows = round_leaderboard(round_) if round_ else []
    return render_template("main/round_leaderboard.html", round=round_, rows=rows)


@bp.route("/leaderboard/tournament")
@login_required
def tournament_leaderboard_view():
    rows = tournament_standings()
    return render_template("main/tournament_leaderboard.html", rows=rows)


@bp.route("/rounds/<int:round_id>/results")
@login_required
def round_results(round_id):
    round_ = Round.query.get_or_404(round_id)

    # Drafts are admin-only - regular users shouldn't even know they exist yet.
    if round_.status == ROUND_STATUS_DRAFT and not current_user.is_admin:
        abort(404)
    if not round_.is_locked:
        abort(403)

    fixtures = round_.fixtures.all()
    predictions_by_fixture = {
        fixture.id: {p.user_id: p for p in fixture.predictions} for fixture in fixtures
    }
    users = User.query.order_by(User.username.asc()).all()

    return render_template(
        "main/round_results.html",
        round=round_,
        fixtures=fixtures,
        users=users,
        predictions_by_fixture=predictions_by_fixture,
    )


@bp.route("/rounds/<int:round_id>/live-scores")
@login_required
def round_live_scores(round_id):
    """JSON feed of current scores/match-minutes, polled by the results page's auto-refresh.

    Mirrors round_results' visibility rules (drafts stay admin-only, results are only
    shown once the round has locked) since this exposes the same underlying data.
    """
    round_ = Round.query.get_or_404(round_id)

    if round_.status == ROUND_STATUS_DRAFT and not current_user.is_admin:
        abort(404)
    if not round_.is_locked:
        abort(403)

    return jsonify(
        fixtures=[
            {
                "id": fixture.id,
                "status": fixture.status,
                "is_live": fixture.is_live,
                "is_finished": fixture.is_finished,
                "home_score": fixture.home_score_90,
                "away_score": fixture.away_score_90,
                "minute": fixture.elapsed_minutes,
                "is_knockout": fixture.is_knockout,
                "winner_team": (
                    fixture.home_team
                    if fixture.winner == "HOME"
                    else fixture.away_team if fixture.winner == "AWAY" else None
                ),
            }
            for fixture in round_.fixtures.all()
        ]
    )
