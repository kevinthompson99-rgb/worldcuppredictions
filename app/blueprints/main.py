import os
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.finance import (
    is_opted_in,
    round_financial_summary,
    round_pot,
    season_financial_table,
)
from app.forms import CSRFForm
from app.leaderboards import tournament_standings
from app.teams import flag_for
from app.models import ROUND_STATUS_DRAFT, Fixture, Prediction, Round, RoundEntry, User
from app.round_helpers import get_active_round, get_round_for_leaderboard
from app.scoring import calculate_points, POINTS_CORRECT_RESULT, POINTS_EXACT_SCORE

bp = Blueprint("main", __name__)

# Used to colour each player's avatar circle - picked deterministically from the
# user's id so it stays stable across visits without needing to store a preference.
_AVATAR_COLORS = (
    "#e63946", "#f3722c", "#f9c74f", "#90be6d",
    "#43aa8b", "#577590", "#277da1", "#9d4edd",
)


def _avatar(user):
    initials = user.display_name[:2].upper() if len(user.display_name) > 1 else user.display_name[:1].upper()
    return {"initials": initials, "color": _AVATAR_COLORS[user.id % len(_AVATAR_COLORS)]}


def _cell(user, prediction, fixture, locked, is_me):
    """Build the per-(user, fixture) cell the read-only players grid renders.

    `status` drives both the icon and highlight, and is one of:
      hidden   - round hasn't locked yet, so this prediction is concealed (own
                 included - the dedicated My Predictions screen is where a user
                 reviews/edits their own picks before the deadline)
      no_pick  - round has locked and this user never made a prediction
      pending  - round has locked, prediction is visible, but the fixture has no score yet
      wrong    - scored 0 against the current/final score (wrong result)
      correct  - scored the correct-result points against the current/final score (not exact)
      exact    - matches the current/final score exactly
    """
    if not locked:
        status = "hidden"
    elif prediction is None:
        status = "no_pick"
    elif prediction.points is None:
        status = "pending"
    elif prediction.points == POINTS_EXACT_SCORE:
        status = "exact"
    elif prediction.points:
        status = "correct"
    else:
        status = "wrong"

    return {"user_id": user.id, "fixture_id": fixture.id, "prediction": prediction, "status": status}


@bp.route("/sw.js")
def service_worker():
    """Serve the service worker from the root (scope covers the whole app).

    Prepends a deploy-time stamp so the file bytes change on every process restart
    (= every Railway deploy), letting the browser detect the update automatically
    without requiring a manual CACHE_NAME bump in sw.js.
    """
    sw_path = os.path.join(current_app.static_folder, "sw.js")
    with open(sw_path, "r", encoding="utf-8") as f:
        content = f.read()
    deploy_time = current_app.config.get("DEPLOY_TIME", 0)
    versioned = f"/* deploy:{deploy_time} */\n" + content
    return current_app.response_class(
        versioned,
        mimetype="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.players"))
    return render_template("main/index.html")


@bp.route("/about")
def about():
    return render_template("main/about.html")


@bp.route("/players")
@login_required
def players():
    """The home screen: a read-only gameweek overview - pot size, who's opted in,
    and everyone's predictions/results once the round locks (clock icons hide them
    before that). Entering predictions lives on its own screen (main.my_predictions),
    and the leaderboards on theirs (main.leaderboard) - see NOTES.md for why the
    old multi-page layout was retired in favour of these few focused screens.
    """
    round_ = get_active_round()
    fixtures = round_.fixtures.all() if round_ is not None else []
    locked = round_ is not None and round_.is_locked

    opted_in = is_opted_in(current_user, round_) if round_ is not None else False
    opt_in_open = round_ is not None and not locked and not opted_in
    opt_in_form = CSRFForm() if opt_in_open else None

    users = []
    if round_ is not None:
        entrant_ids = {entry.user_id for entry in round_.entries.filter_by(opted_in=True)}
        users = [user for user in User.query.order_by(User.display_name.asc()).all() if user.id in entrant_ids]
        # The logged-in user's column always comes first, then everyone else
        # alphabetically (the query above already sorts by display_name).
        users.sort(key=lambda user: user.id != current_user.id)

    predictions = {}
    if fixtures:
        fixture_ids = [fixture.id for fixture in fixtures]
        for prediction in Prediction.query.filter(Prediction.fixture_id.in_(fixture_ids)):
            predictions[(prediction.user_id, prediction.fixture_id)] = prediction

    grid = [
        {
            "fixture": fixture,
            "cells": [
                _cell(user, predictions.get((user.id, fixture.id)), fixture, locked, user.id == current_user.id)
                for user in users
            ],
        }
        for fixture in fixtures
    ]

    standings = tournament_standings()

    # All kick-off times for the round, for the JS countdown - it works out which
    # match (if any) is currently in its live window and which is next purely from
    # these timestamps, see players.html.
    fixture_kickoffs = [f.kickoff_at.strftime("%Y-%m-%dT%H:%M:%SZ") for f in fixtures]

    has_live_fixtures = any(f.is_live for f in fixtures)
    live_fixture_ids = [f.id for f in fixtures if f.is_live]

    return render_template(
        "main/players.html",
        round=round_,
        fixtures=fixtures,
        users=users,
        total_users=User.query.count(),
        grid=grid,
        locked=locked,
        avatars={user.id: _avatar(user) for user in users},
        totals={user.id: points for user, points in standings},
        opted_in=opted_in,
        opt_in_open=opt_in_open,
        opt_in_form=opt_in_form,
        stake_amount=round_.stake_amount if round_ is not None else None,
        pot=round_pot(len(users), round_.stake_amount) if round_ is not None else None,
        fixture_kickoffs=fixture_kickoffs,
        has_live_fixtures=has_live_fixtures,
        live_fixture_ids=live_fixture_ids,
    )


@bp.route("/round/opt-in", methods=["POST"])
@login_required
def round_opt_in():
    """Opt the current user in to the active round's pot - a one-way commitment.

    Open from round creation until the (shared prediction/opt-in) deadline; once
    in, a player can't back out (and once locked, stakes are final regardless).
    """
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400)

    round_ = get_active_round()
    if round_ is None or round_.is_locked:
        flash("Opt-in is closed for this round.", "warning")
        return redirect(url_for("main.players"))

    entry = RoundEntry.query.filter_by(user_id=current_user.id, round_id=round_.id).first()
    if entry is None:
        entry = RoundEntry(user_id=current_user.id, round_id=round_.id, opted_in=True)
        db.session.add(entry)
    elif not entry.opted_in:
        entry.opted_in = True
    else:
        return redirect(url_for("main.players"))

    entry.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"You're in! £{round_.stake_amount:.2f} added to the pot for {round_.name}.", "success")
    return redirect(url_for("main.players"))


@bp.route("/dashboard")
@login_required
def dashboard():
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


@bp.route("/leaderboard")
@login_required
def leaderboard():
    """The dedicated leaderboard screen: this round's pot standings (opted-in
    players only, with each one's financial result) plus the season-long table
    (cumulative points and running balance for everyone who's taken part).
    """
    round_ = get_round_for_leaderboard()

    return render_template(
        "main/leaderboard.html",
        round=round_,
        financial_summary=round_financial_summary(round_) if round_ is not None else None,
        season_financial_rows=season_financial_table(),
    )


@bp.route("/leaderboard/round")
@login_required
def round_leaderboard_view():
    # Moved to its own dedicated screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.leaderboard"))


@bp.route("/leaderboard/tournament")
@login_required
def tournament_leaderboard_view():
    # Moved to its own dedicated screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.leaderboard"))


@bp.route("/rounds/<int:round_id>/results")
@login_required
def round_results(round_id):
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


def _live_pred_status(prediction, fixture):
    """Status string for a prediction against a live fixture's current score."""
    if prediction is None:
        return "no_pick"
    points = calculate_points(prediction, fixture)
    if points is None:
        return "pending"
    if points == POINTS_EXACT_SCORE:
        return "exact"
    if points == POINTS_CORRECT_RESULT:
        return "correct"
    return "wrong"


@bp.route("/live/<int:fixture_id>")
@login_required
def live_match(fixture_id):
    """Full-screen live match view: scoreboard + all players' predictions for this fixture.

    Accessible for any fixture in the active locked round that is currently in-play
    or has finished.  The client-side JS handles the 30-minute post-FT countdown
    before auto-navigating back to the home screen.
    """
    fixture = Fixture.query.get_or_404(fixture_id)
    round_ = get_active_round()

    if round_ is None or fixture.round_id != round_.id or not round_.is_locked:
        return redirect(url_for("main.players"))
    if not (fixture.is_live or fixture.is_finished):
        return redirect(url_for("main.players"))

    all_fixtures = round_.fixtures.order_by(Fixture.kickoff_at).all()

    # Only currently-live fixtures appear in the swipe carousel / dots.
    live_fixtures = [f for f in all_fixtures if f.is_live]
    live_ids = [f.id for f in live_fixtures]
    current_idx = live_ids.index(fixture_id) if fixture_id in live_ids else -1
    prev_fixture_id = live_ids[current_idx - 1] if current_idx > 0 else None
    next_fixture_id = live_ids[current_idx + 1] if 0 <= current_idx < len(live_ids) - 1 else None

    entrant_ids = {entry.user_id for entry in round_.entries.filter_by(opted_in=True)}
    users = User.query.filter(User.id.in_(entrant_ids)).order_by(User.display_name.asc()).all()

    preds = {p.user_id: p for p in Prediction.query.filter_by(fixture_id=fixture_id)}
    pred_entries = [
        {
            "user": user,
            "prediction": preds.get(user.id),
            "status": _live_pred_status(preds.get(user.id), fixture),
        }
        for user in users
    ]

    return render_template(
        "main/live_match.html",
        fixture=fixture,
        round=round_,
        pred_entries=pred_entries,
        live_ids=live_ids,
        current_idx=current_idx,
        prev_fixture_id=prev_fixture_id,
        next_fixture_id=next_fixture_id,
        home_flag=flag_for(fixture.home_team),
        away_flag=flag_for(fixture.away_team),
    )


@bp.route("/scores/live")
@login_required
def scores_live():
    """Lightweight JSON endpoint for pull-to-refresh and HT/FT auto-update on the home screen.

    Returns fixture scores for the active round without requiring the caller to know the
    round ID. Includes team names so the client can re-render the fixture display in the
    correct live/FT format without a full page reload.
    """
    round_ = get_active_round()
    if round_ is None or not round_.is_locked:
        return jsonify(fixtures=[], is_live_window=False, totals={})

    fixtures = round_.fixtures.all()
    has_live = any(f.is_live for f in fixtures)

    return jsonify(
        is_live_window=has_live,
        fixtures=[
            {
                "id": fixture.id,
                "status": fixture.status,
                "is_live": fixture.is_live,
                "is_finished": fixture.is_finished,
                "kickoff_at": fixture.kickoff_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "home_score": fixture.home_score_90,
                "away_score": fixture.away_score_90,
                "home_team": fixture.home_short_name or fixture.home_team,
                "away_team": fixture.away_short_name or fixture.away_team,
                "home_flag": flag_for(fixture.home_team),
                "away_flag": flag_for(fixture.away_team),
                "minute": fixture.current_minute,
                "injury_time": fixture.current_injury_time,
            }
            for fixture in fixtures
        ],
        # Live-scored points can move during a match - sent so the players grid's
        # per-user totals stay current on every refresh, not just at page load.
        totals={str(user.id): points for user, points in tournament_standings()},
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
                "minute": fixture.current_minute,
                "injury_time": fixture.current_injury_time,
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
