"""Shared read-only prediction-grid builder for a gameweek.

Used by both the live players grid (main.players, while a gameweek is active)
and the results history archive (main.history, for COMPLETE gameweeks) - the
row/cell structure and colour-coding rules are identical, so it's built once
here rather than duplicated per route.
"""

from app.models import GAMEWEEK_STATUS_COMPLETE, Prediction
from app.scoring import POINTS_EXACT_SCORE


def _cell(user, prediction, fixture, locked):
    """Build the per-(user, fixture) cell the read-only grid renders.

    `status` drives both the icon and highlight, and is one of:
      hidden   - gameweek hasn't locked yet, so this prediction is concealed
      no_pick  - gameweek has locked and this user never made a prediction
      pending  - gameweek has locked, prediction is visible, but the fixture has no score yet
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


def _sort_users_by_gameweek_result(users, predictions):
    """Order `users` in place by this gameweek's own result: total points scored
    descending, exact scores as the tiebreaker, then name - so a COMPLETE
    gameweek's grid reads left-to-right as its own mini leaderboard, winner first.

    `predictions` is build_grid's own {(user_id, fixture_id): Prediction} map,
    already scoped to this gameweek's fixtures.
    """
    points_by_user = {}
    exact_by_user = {}
    for (user_id, _fixture_id), prediction in predictions.items():
        points_by_user[user_id] = points_by_user.get(user_id, 0) + (prediction.points or 0)
        if prediction.points == POINTS_EXACT_SCORE:
            exact_by_user[user_id] = exact_by_user.get(user_id, 0) + 1

    users.sort(key=lambda user: (
        -points_by_user.get(user.id, 0),
        -exact_by_user.get(user.id, 0),
        user.display_name,
    ))


def build_grid(gameweek, users):
    """List of {"fixture": Fixture, "cells": [...]} rows for a gameweek's read-only
    predictions grid - one row per fixture, one cell per user in `users`.

    For a COMPLETE gameweek, `users` is re-sorted in place by that gameweek's own
    result before the columns are built (see _sort_users_by_gameweek_result) - the
    caller's `users` list is the same object handed to the template for the header
    row, so both stay in sync automatically. A gameweek that isn't COMPLETE yet
    (e.g. the live active one) keeps whatever order the caller passed in.
    """
    fixtures = gameweek.fixtures.all()
    locked = gameweek.is_locked

    predictions = {}
    if fixtures:
        fixture_ids = [fixture.id for fixture in fixtures]
        for prediction in Prediction.query.filter(Prediction.fixture_id.in_(fixture_ids)):
            predictions[(prediction.user_id, prediction.fixture_id)] = prediction

    if gameweek.status == GAMEWEEK_STATUS_COMPLETE:
        _sort_users_by_gameweek_result(users, predictions)

    return [
        {
            "fixture": fixture,
            "cells": [_cell(user, predictions.get((user.id, fixture.id)), fixture, locked) for user in users],
        }
        for fixture in fixtures
    ]
