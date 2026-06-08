from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.finance import (
    STAKE_AMOUNT,
    is_opted_in,
    round_financial_summary,
    round_pot,
    season_financial_table,
)
from app.forms import CSRFForm, build_prediction_form
from app.leaderboards import round_leaderboard, tournament_standings
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
    initials = user.username[:2].upper() if len(user.username) > 1 else user.username[:1].upper()
    return {"initials": initials, "color": _AVATAR_COLORS[user.id % len(_AVATAR_COLORS)]}


def _cell(user, prediction, fixture, locked, is_me):
    """Build the per-(user, fixture) cell the players grid renders.

    `status` drives both the icon/highlight/edit-state and is one of:
      editable - this is the current user's own cell and the round hasn't locked -
                 it's rendered as score inputs rather than a read-only display
      hidden   - round hasn't locked yet, so another user's prediction is concealed
      no_pick  - round has locked and this user never made a prediction
      pending  - round has locked, prediction is visible, but the match hasn't finished
      wrong    - finished, prediction scored 0 (wrong result)
      correct  - finished, prediction scored the correct-result points (not exact)
      exact    - finished, prediction matched the score exactly
    """
    if not locked:
        status = "editable" if is_me else "hidden"
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


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.players"))
    return render_template("main/index.html")


@bp.route("/players", methods=["GET", "POST"])
@login_required
def players():
    """The single home screen: the players grid, plus the current user's own
    prediction entry, opt-in/pot status, round info, and leaderboards - everything
    a regular user needs lives here (see NOTES.md for why the old multi-page layout
    was retired).
    """
    round_ = get_active_round()
    fixtures = round_.fixtures.all() if round_ is not None else []
    locked = round_ is not None and round_.is_locked

    opted_in = is_opted_in(current_user, round_) if round_ is not None else False
    opt_in_open = round_ is not None and not locked
    opt_in_form = CSRFForm() if opt_in_open else None

    users = []
    if round_ is not None:
        entrant_ids = {entry.user_id for entry in round_.entries.filter_by(opted_in=True)}
        users = [user for user in User.query.order_by(User.username.asc()).all() if user.id in entrant_ids]

    predictions = {}
    if fixtures:
        fixture_ids = [fixture.id for fixture in fixtures]
        for prediction in Prediction.query.filter(Prediction.fixture_id.in_(fixture_ids)):
            predictions[(prediction.user_id, prediction.fixture_id)] = prediction

    form = None
    if fixtures and not locked and opted_in:
        form = build_prediction_form(fixtures)

        if form.validate_on_submit():
            # Re-check the lock at submission time - the deadline may have passed mid-edit.
            if round_.is_locked:
                flash("Sorry, predictions for this round just locked. Your changes were not saved.", "danger")
                return redirect(url_for("main.players"))

            for fixture in fixtures:
                home = getattr(form, f"home_{fixture.id}").data
                away = getattr(form, f"away_{fixture.id}").data
                prediction = predictions.get((current_user.id, fixture.id))
                if prediction is None:
                    prediction = Prediction(user_id=current_user.id, fixture_id=fixture.id)
                    db.session.add(prediction)
                prediction.predicted_home = home
                prediction.predicted_away = away
                prediction.updated_at = datetime.utcnow()

            db.session.commit()
            flash("Your predictions have been saved.", "success")
            return redirect(url_for("main.players"))

        if request.method == "GET":
            for fixture in fixtures:
                prediction = predictions.get((current_user.id, fixture.id))
                if prediction is not None:
                    getattr(form, f"home_{fixture.id}").data = prediction.predicted_home
                    getattr(form, f"away_{fixture.id}").data = prediction.predicted_away
    elif fixtures and not locked and not opted_in and request.method == "POST":
        flash(f"Join this round (£{STAKE_AMOUNT:.2f}) before you can submit predictions.", "warning")
        return redirect(url_for("main.players"))

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
    my_prediction_count = sum(1 for fixture in fixtures if (current_user.id, fixture.id) in predictions)

    return render_template(
        "main/players.html",
        round=round_,
        users=users,
        grid=grid,
        locked=locked,
        form=form,
        my_prediction_count=my_prediction_count,
        avatars={user.id: _avatar(user) for user in users},
        totals={user.id: points for user, points in standings},
        tournament_rows=standings,
        opted_in=opted_in,
        opt_in_open=opt_in_open,
        opt_in_form=opt_in_form,
        stake_amount=STAKE_AMOUNT,
        pot=round_pot(len(users)) if round_ is not None else None,
        financial_summary=round_financial_summary(round_) if round_ is not None else None,
        season_financial_rows=season_financial_table(),
    )


@bp.route("/round/opt-in", methods=["POST"])
@login_required
def round_opt_in():
    """Toggle the current user's opt-in status for the active round's pot.

    Open from round creation until the (shared prediction/opt-in) deadline - once
    locked, stakes are final so the toggle is refused even if the form is resubmitted.
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
        flash(f"You're in! £{STAKE_AMOUNT:.2f} added to the pot for {round_.name}.", "success")
    elif entry.opted_in:
        entry.opted_in = False
        flash(f"You've left {round_.name} - you're no longer part of the pot.", "info")
    else:
        entry.opted_in = True
        flash(f"You're in! £{STAKE_AMOUNT:.2f} added to the pot for {round_.name}.", "success")

    entry.updated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("main.players"))


@bp.route("/dashboard")
@login_required
def dashboard():
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


@bp.route("/leaderboard/round")
@login_required
def round_leaderboard_view():
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


@bp.route("/leaderboard/round/live")
@login_required
def round_leaderboard_live():
    """JSON feed of round/tournament points, polled by the leaderboard's auto-refresh.

    As fixtures finish and get (re)scored - including mid-round, while other matches are
    still live - this lets the standings update in place on the same 3-minute cadence as
    the live score feed (main.round_live_scores), without a manual page reload.
    """
    round_ = get_round_for_leaderboard()
    rows = round_leaderboard(round_) if round_ is not None else []

    return jsonify(
        round_id=round_.id if round_ is not None else None,
        rows=[
            {
                "user_id": user.id,
                "username": user.username,
                "round_points": round_points,
                "tournament_points": tournament_points,
            }
            for user, round_points, tournament_points in rows
        ],
    )


@bp.route("/leaderboard/tournament")
@login_required
def tournament_leaderboard_view():
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


@bp.route("/rounds/<int:round_id>/results")
@login_required
def round_results(round_id):
    # Folded into the players home screen - keep the URL alive for old bookmarks/links.
    return redirect(url_for("main.players"))


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
