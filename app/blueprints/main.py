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
from app.models import ROUND_STATUS_DRAFT, Prediction, Round, RoundEntry, User
from app.round_helpers import get_active_round, get_round_for_leaderboard
from app.scoring import POINTS_EXACT_SCORE

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
      pending  - round has locked, prediction is visible, but the match hasn't finished
      wrong    - finished, prediction scored 0 (wrong result)
      correct  - finished, prediction scored the correct-result points (not exact)
      exact    - finished, prediction matched the score exactly
    """
    if not locked:
        status = "hidden"
    elif prediction is None:
        status = "no_pick"
    elif not fixture.is_finished:
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

    upcoming = [f for f in fixtures if not f.is_finished and not f.is_live]
    next_kickoff = (
        min(upcoming, key=lambda f: f.kickoff_at).kickoff_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        if upcoming else None
    )

    has_live_fixtures = any(f.is_live for f in fixtures)

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
        next_kickoff=next_kickoff,
        has_live_fixtures=has_live_fixtures,
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
        return jsonify(fixtures=[], is_live_window=False)

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
                "home_score": fixture.home_score_90,
                "away_score": fixture.away_score_90,
                "home_team": fixture.home_short_name or fixture.home_team,
                "away_team": fixture.away_short_name or fixture.away_team,
                "home_flag": flag_for(fixture.home_team),
                "away_flag": flag_for(fixture.away_team),
            }
            for fixture in fixtures
        ],
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
