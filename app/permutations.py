"""What-needs-to-happen permutations for a gameweek's final unplayed fixture.

Once every fixture but one in a gameweek has finished, every opted-in player's
final gameweek total - and so who wins that week's pot - hinges entirely on
that last result. This works out, for each player, which final scorelines
would let them win or share the pot, and turns that into a plain-English
summary for a push notification (see app/scheduler.py's 15-minute job, which
fires this ~1 hour before that final kick-off).

Nothing here is persisted - it's all derived on the fly from GameweekEntry +
Prediction, the same read-only style as app.leaderboards/app.finance.
"""

from sqlalchemy import func

from app.extensions import db
from app.models import OUTCOME_AWAY, OUTCOME_DRAW, OUTCOME_HOME, Prediction, User
from app.scoring import POINTS_CORRECT_RESULT, POINTS_EXACT_SCORE
from app.time_utils import to_london

_BUCKET_LABELS = {
    OUTCOME_HOME: "any other home win",
    OUTCOME_AWAY: "any other away win",
    OUTCOME_DRAW: "any other draw",
}


def _outcome(home, away):
    if home > away:
        return OUTCOME_HOME
    if away > home:
        return OUTCOME_AWAY
    return OUTCOME_DRAW


def _join_list(items, conjunction):
    """Join 1-3+ items as "A", "A & B", or "A, B & C" (Oxford-comma-free) - same
    convention as admin._notify_gameweek_winner, just reused here for both name
    lists and scoreline lists (with a caller-chosen conjunction: "&" or "or").
    """
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _build_scenarios(final_predictions):
    """Every hypothetical final-fixture result worth considering: one per unique
    scoreline actually predicted by an entrant, plus one generic "any other"
    scenario per outcome bucket (home win / away win / draw) not tied to a
    specific score - covering every possibility even if nobody guessed it.
    """
    exact_scorelines = sorted({
        (prediction.predicted_home, prediction.predicted_away)
        for prediction in final_predictions.values()
    })

    scenarios = [
        {
            "kind": "exact",
            "home": home,
            "away": away,
            "outcome": _outcome(home, away),
            "label": f"{home}-{away}",
        }
        for home, away in exact_scorelines
    ]
    scenarios.extend(
        {"kind": "bucket", "home": None, "away": None, "outcome": outcome, "label": _BUCKET_LABELS[outcome]}
        for outcome in (OUTCOME_HOME, OUTCOME_AWAY, OUTCOME_DRAW)
    )
    return scenarios


def _score_under_scenario(prediction, scenario):
    """Points a (possibly missing) final-fixture prediction earns under one
    hypothetical result - the same 0/6/16 rule as app.scoring.calculate_points,
    just against a scenario instead of a real recorded score.
    """
    if prediction is None:
        return 0
    if (
        scenario["kind"] == "exact"
        and prediction.predicted_home == scenario["home"]
        and prediction.predicted_away == scenario["away"]
    ):
        return POINTS_EXACT_SCORE
    if _outcome(prediction.predicted_home, prediction.predicted_away) == scenario["outcome"]:
        return POINTS_CORRECT_RESULT
    return 0


def _summarize(user, winning, total_scenarios, users_by_id):
    """Plain-English summary of one contender's winning scenarios - see the
    five patterns in calculate_final_permutations' docstring/callers.
    """
    if len(winning) == total_scenarios:
        return "wins with any result"

    if len(winning) == 1:
        scenario_result = winning[0]
        scenario = scenario_result["scenario"]
        other_winner_ids = scenario_result["winners"] - {user.id}

        if not other_winner_ids:
            return f"wins outright with {scenario['label']}"

        other_names = _join_list(
            sorted(users_by_id[uid].display_name for uid in other_winner_ids), "&"
        )
        return f"shares with {other_names} if {scenario['label']}"

    # More than one, but not every, winning scenario - no single pattern in the
    # spec covers this, so list out what still needs to happen.
    labels = [scenario_result["scenario"]["label"] for scenario_result in winning]
    return f"needs {_join_list(labels, 'or')} to win"


def calculate_final_permutations(gameweek):
    """Work out every opted-in player's path to winning `gameweek`'s pot, based
    on the one fixture still to be played.

    Only meaningful with exactly one unfinished fixture left in the gameweek -
    returns None otherwise (nothing, or too much, still to resolve), and also
    if nobody is opted in (nothing to determine).

    Returns {
        "final_fixture": Fixture,
        "contenders": [(User, summary_string), ...] - ordered by current
            (pre-final-fixture) gameweek points descending,
        "eliminated": [display_name, ...] - players with no winning scenario,
        "already_won": bool - True if one player wins alone in every scenario,
            i.e. the pot's outcome no longer depends on the final result at all,
    }
    """
    fixtures = gameweek.fixtures.all()
    unfinished = [fixture for fixture in fixtures if not fixture.is_finished]
    if len(unfinished) != 1:
        return None
    final_fixture = unfinished[0]

    entrant_ids = {entry.user_id for entry in gameweek.entries.filter_by(opted_in=True)}
    if not entrant_ids:
        return None

    users = User.query.filter(User.id.in_(entrant_ids)).order_by(User.display_name.asc()).all()
    users_by_id = {user.id: user for user in users}

    # Current total excludes the final fixture entirely - even if it's live and
    # already has provisional points recorded, those don't count here, since
    # every scenario below computes its own hypothetical final-fixture score.
    other_fixture_ids = [fixture.id for fixture in fixtures if fixture.id != final_fixture.id]
    current_points = {user.id: 0 for user in users}
    if other_fixture_ids:
        for user_id, points in (
            db.session.query(Prediction.user_id, func.coalesce(func.sum(Prediction.points), 0))
            .filter(Prediction.fixture_id.in_(other_fixture_ids), Prediction.user_id.in_(entrant_ids))
            .group_by(Prediction.user_id)
            .all()
        ):
            current_points[user_id] = int(points)

    final_predictions = {
        prediction.user_id: prediction
        for prediction in Prediction.query.filter_by(fixture_id=final_fixture.id).filter(
            Prediction.user_id.in_(entrant_ids)
        )
    }

    scenarios = _build_scenarios(final_predictions)

    scenario_results = []
    for scenario in scenarios:
        totals = {
            user.id: current_points[user.id] + _score_under_scenario(final_predictions.get(user.id), scenario)
            for user in users
        }
        top_score = max(totals.values())
        winners = {user_id for user_id, total in totals.items() if total == top_score}
        scenario_results.append({"scenario": scenario, "totals": totals, "winners": winners})

    # "Already won": the same single player wins outright (no tie) in every
    # single scenario, so no possible final result changes who gets the pot.
    already_won_user_id = None
    solo_winner_ids = [next(iter(sr["winners"])) for sr in scenario_results if len(sr["winners"]) == 1]
    if len(solo_winner_ids) == len(scenario_results) and len({*solo_winner_ids}) == 1:
        already_won_user_id = solo_winner_ids[0]

    contenders = []
    eliminated = []

    if already_won_user_id is not None:
        winner = users_by_id[already_won_user_id]
        contenders.append((winner, "has already won the pot"))
        eliminated = sorted(user.display_name for user in users if user.id != already_won_user_id)
    else:
        total_scenarios = len(scenario_results)
        for user in users:
            winning = [sr for sr in scenario_results if user.id in sr["winners"]]
            if not winning:
                eliminated.append(user.display_name)
                continue
            contenders.append((user, _summarize(user, winning, total_scenarios, users_by_id)))

        contenders.sort(key=lambda entry: (-current_points[entry[0].id], entry[0].display_name))
        eliminated.sort()

    return {
        "final_fixture": final_fixture,
        "contenders": contenders,
        "eliminated": eliminated,
        "already_won": already_won_user_id is not None,
    }


def build_permutations_notification(result):
    """(title, body) push-notification strings for a calculate_final_permutations() result."""
    fixture = result["final_fixture"]
    kickoff = to_london(fixture.kickoff_at, "%-I:%M%p").lower()
    title = f"Final Match: {fixture.home_team} v {fixture.away_team} ({kickoff} BST)"

    if result["already_won"]:
        winner, _summary = result["contenders"][0]
        body = f"{winner.display_name} has already won the pot — enjoy the match!"
        return title, body

    if not result["contenders"]:
        return title, "It's all over — nobody can change the result now. Enjoy the final match!"

    lines = [f"{user.display_name} {summary}" for user, summary in result["contenders"]]
    body = (
        f"{len(result['contenders'])} players still in contention:\n\n"
        + "\n".join(lines)
        + "\n\nAll other players are now on the bench"
    )
    return title, body
