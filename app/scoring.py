"""Pure scoring logic, kept separate from the models so the rules are easy to find and test.

Rules (from the spec):
  - Correct result (win/draw/loss): 6 points
  - Correct exact score: 16 points total (this includes the 6 for the result, not on top of it)
  - Wrong result: 0 points

Predictions are always judged against the 90-minute score (`Fixture.home_score_90` /
`away_score_90`), for both group and knockout fixtures. A draw after 90 minutes is a
valid result in its own right - what happens in extra time or penalties has no bearing
on scoring.
"""

from app.models import Fixture, Prediction

POINTS_CORRECT_RESULT = 6
POINTS_EXACT_SCORE = 16


def calculate_points(prediction: Prediction, fixture: Fixture):
    """Return the points a prediction earns for a finished fixture, or None if not yet playable."""
    if not fixture.is_finished:
        return None

    exact_match = (
        prediction.predicted_home == fixture.home_score_90
        and prediction.predicted_away == fixture.away_score_90
    )
    if exact_match:
        return POINTS_EXACT_SCORE

    if prediction.predicted_outcome == fixture.result_outcome:
        return POINTS_CORRECT_RESULT

    return 0


def score_fixture(fixture: Fixture) -> int:
    """Calculate and persist points for every prediction on a finished fixture.

    Returns the number of predictions updated. Caller is responsible for committing.
    """
    if not fixture.is_finished:
        return 0

    updated = 0
    for prediction in fixture.predictions:
        prediction.points = calculate_points(prediction, fixture)
        updated += 1
    return updated
