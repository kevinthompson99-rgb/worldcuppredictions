"""Shared read-only prediction-grid builder for a gameweek.

Used by both the live players grid (main.players, while a gameweek is active)
and the results history archive (main.history, for COMPLETE gameweeks) - the
row/cell structure and colour-coding rules are identical, so it's built once
here rather than duplicated per route.
"""

from app.models import Prediction
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


def build_grid(gameweek, users):
    """List of {"fixture": Fixture, "cells": [...]} rows for a gameweek's read-only
    predictions grid - one row per fixture, one cell per user in `users`.
    """
    fixtures = gameweek.fixtures.all()
    locked = gameweek.is_locked

    predictions = {}
    if fixtures:
        fixture_ids = [fixture.id for fixture in fixtures]
        for prediction in Prediction.query.filter(Prediction.fixture_id.in_(fixture_ids)):
            predictions[(prediction.user_id, prediction.fixture_id)] = prediction

    return [
        {
            "fixture": fixture,
            "cells": [_cell(user, predictions.get((user.id, fixture.id)), fixture, locked) for user in users],
        }
        for fixture in fixtures
    ]
