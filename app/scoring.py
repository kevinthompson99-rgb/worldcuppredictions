"""Pure scoring logic, kept separate from the models so the rules are easy to find and test.

Rules (from the spec):
  - Correct result (win/draw): 6 points
  - Correct exact score: 16 points total (this includes the 6 for the result, not on top of it)
  - Wrong result: 0 points

Knockout nuance: predictions are always compared against the 90-minute score, but in
knockout rounds there are no draws - the match always produces a winner via extra time
or penalties. So "correct result" there means correctly picking the side that *advances*,
regardless of the 90-minute scoreline or the user's predicted scoreline being a draw.
"""

from app.models import OUTCOME_AWAY, OUTCOME_DRAW, OUTCOME_HOME, Fixture, Prediction

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

    if fixture.is_knockout:
        if _knockout_result_correct(prediction, fixture):
            return POINTS_CORRECT_RESULT
    else:
        if prediction.predicted_outcome == fixture.result_outcome:
            return POINTS_CORRECT_RESULT

    return 0


def _knockout_result_correct(prediction: Prediction, fixture: Fixture) -> bool:
    """In knockout matches, "correct result" = correctly picking the team that advances.

    A user who predicts a draw can't be picking a winner, so they can't earn the
    result points unless their exact score matches (handled separately above).
    """
    if fixture.winner not in (OUTCOME_HOME, OUTCOME_AWAY):
        return False
    if prediction.predicted_outcome == OUTCOME_DRAW:
        return False
    return prediction.predicted_outcome == fixture.winner


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
