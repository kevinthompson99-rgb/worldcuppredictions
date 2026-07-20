"""Pure scoring logic, kept separate from the models so the rules are easy to find and test.

Rules (from the spec):
  - Correct result (win/draw/loss): 6 points
  - Correct exact score: 16 points total (this includes the 6 for the result, not on top of it)
  - Wrong result: 0 points

Predictions are judged against the fixture's full-time score (`Fixture.home_score`/
`away_score`) - every Premier League match is decided at 90 minutes plus stoppage time,
so a draw is a valid result in its own right, scored the same as any other outcome.

Scoring runs against whatever score is currently stored, live or final (see
`app.sync.sync_fixtures_and_results`), so points - and gameweek totals - move during a
match as the score changes, settling once the fixture is FINISHED/AWARDED.
"""

from app.models import Fixture, Prediction

POINTS_CORRECT_RESULT = 6
POINTS_EXACT_SCORE = 16


def calculate_points(prediction: Prediction, fixture: Fixture):
    """Return the points a prediction earns against the fixture's current score.

    Returns None if the fixture doesn't have a score yet. Once it does, this is scored
    the same way whether the match is still live or finished - see score_fixture.
    """
    if fixture.home_score is None or fixture.away_score is None:
        return None

    exact_match = (
        prediction.predicted_home == fixture.home_score
        and prediction.predicted_away == fixture.away_score
    )
    if exact_match:
        return POINTS_EXACT_SCORE

    if prediction.predicted_outcome == fixture.result_outcome:
        return POINTS_CORRECT_RESULT

    return 0


def score_fixture(fixture: Fixture) -> int:
    """Calculate and persist points for every prediction against the fixture's current score.

    Called both for finished fixtures and for live ones (so points - and gameweek totals -
    update in real time as the score changes during a match). Returns the number of
    predictions updated. Caller is responsible for committing.
    """
    if fixture.home_score is None or fixture.away_score is None:
        return 0

    updated = 0
    for prediction in fixture.predictions:
        prediction.points = calculate_points(prediction, fixture)
        updated += 1
    return updated
