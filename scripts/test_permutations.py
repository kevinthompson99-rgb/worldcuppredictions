"""Standalone test for app.permutations - builds synthetic gameweek data in an
in-memory database (never touches the real dev/production database) and prints
the full calculate_final_permutations() output plus the notification text it
produces, so the logic can be eyeballed before it ever fires on real data.

Run with:

    python scripts/test_permutations.py
"""

import os
import sys
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from app.extensions import db
from config import Config


def build_test_app():
    """Same minimal DB-only bootstrap as scripts/sanity_check.py, but against a
    fresh in-memory schema this script populates itself (create_all(), not a
    real database) - see that script for why create_app() is avoided here.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        from app import models  # noqa: F401  (registers model metadata with SQLAlchemy)

        db.create_all()
    return app


def make_user(display_name):
    from app.models import User

    user = User(username=display_name.lower(), display_name=display_name, email=None)
    user.set_password("x")
    db.session.add(user)
    return user


def make_gameweek(matchday, name):
    from app.models import GAMEWEEK_STATUS_ACTIVE, Gameweek

    gameweek = Gameweek(matchday=matchday, name=name, status=GAMEWEEK_STATUS_ACTIVE, force_locked=True)
    db.session.add(gameweek)
    return gameweek


def make_finished_fixture(gameweek, home, away, home_score, away_score, hours_ago=48):
    from app.models import Fixture

    fixture = Fixture(
        gameweek_id=gameweek.id,
        home_team=home,
        away_team=away,
        kickoff_at=datetime.utcnow() - timedelta(hours=hours_ago),
        status="FINISHED",
        home_score=home_score,
        away_score=away_score,
    )
    db.session.add(fixture)
    return fixture


def make_unfinished_fixture(gameweek, home, away, kickoff_in_minutes=60):
    from app.models import Fixture

    fixture = Fixture(
        gameweek_id=gameweek.id,
        home_team=home,
        away_team=away,
        kickoff_at=datetime.utcnow() + timedelta(minutes=kickoff_in_minutes),
        status="TIMED",
    )
    db.session.add(fixture)
    return fixture


def opt_in(user, gameweek):
    from app.models import GameweekEntry

    db.session.add(GameweekEntry(user_id=user.id, gameweek_id=gameweek.id, opted_in=True))


def predict(user, fixture, home, away, points=None):
    from app.models import Prediction

    db.session.add(
        Prediction(user_id=user.id, fixture_id=fixture.id, predicted_home=home, predicted_away=away, points=points)
    )


def score_finished_fixture(fixture):
    """Score every Prediction against a fixture that's already been marked
    FINISHED above - mirrors app.scoring.score_fixture without importing the
    app-context-bound relationship lazily (fixture.predictions is fine here).
    """
    from app.scoring import score_fixture

    score_fixture(fixture)


def print_result(label, result):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    if result is None:
        print("calculate_final_permutations() returned None (not exactly one unfinished fixture, or no entrants)")
        return

    fixture = result["final_fixture"]
    print(f"Final fixture: {fixture.home_team} v {fixture.away_team} (kickoff {fixture.kickoff_at} UTC)")
    print(f"already_won: {result['already_won']}")

    print("\nContenders (current points desc):")
    for user, summary in result["contenders"]:
        print(f"  - {user.display_name}: {summary}")

    print("\nEliminated:")
    for name in result["eliminated"]:
        print(f"  - {name}")

    from app.permutations import build_permutations_notification

    title, body = build_permutations_notification(result)
    print("\n--- Notification ---")
    print(f"Title: {title}")
    print("Body:")
    for line in body.split("\n"):
        print(f"  {line}")


def scenario_primary():
    """The scenario the task spec asked for: 3 players, 2 fixtures already
    scored with varied points (exact/correct/wrong split across them), and 1
    final fixture where two players predict the same exact scoreline (a
    genuine tie) and the third predicts a different outcome entirely.
    """
    from app.permutations import calculate_final_permutations

    gameweek = make_gameweek(1, "Gameweek 1")
    alice, bob, carol = make_user("Alice"), make_user("Bob"), make_user("Carol")
    db.session.commit()

    for user in (alice, bob, carol):
        opt_in(user, gameweek)

    # Fixture A: 2-1. Alice exact (16), Bob correct-result (6), Carol wrong (0).
    fixture_a = make_finished_fixture(gameweek, "Team A", "Team B", 2, 1)
    # Fixture B: 0-0. Alice correct-result (6), Bob wrong (0), Carol exact (16).
    fixture_b = make_finished_fixture(gameweek, "Team C", "Team D", 0, 0)
    db.session.commit()

    predict(alice, fixture_a, 2, 1)   # exact -> 16
    predict(bob, fixture_a, 1, 0)     # correct result (home win) -> 6
    predict(carol, fixture_a, 0, 0)   # wrong -> 0
    predict(alice, fixture_b, 1, 1)   # correct result (draw) -> 6
    predict(bob, fixture_b, 2, 0)     # wrong -> 0
    predict(carol, fixture_b, 0, 0)   # exact -> 16
    db.session.commit()
    score_finished_fixture(fixture_a)
    score_finished_fixture(fixture_b)
    db.session.commit()
    # Current totals going into the final fixture: Alice 22, Bob 6, Carol 16.

    final_fixture = make_unfinished_fixture(gameweek, "Team E", "Team F")
    db.session.commit()

    predict(alice, final_fixture, 2, 1)  # home win
    predict(bob, final_fixture, 1, 1)    # draw
    predict(carol, final_fixture, 2, 1)  # same exact scoreline as Alice - a genuine tie scenario
    db.session.commit()

    result = calculate_final_permutations(gameweek)
    print_result(
        "SCENARIO A - primary (3 players, tie on one exact scoreline, one player eliminated)",
        result,
    )


def scenario_already_won():
    """A player far enough ahead that no possible final result changes the outcome."""
    from app.permutations import calculate_final_permutations

    gameweek = make_gameweek(2, "Gameweek 2")
    dana, eve = make_user("Dana"), make_user("Eve")
    db.session.commit()
    for user in (dana, eve):
        opt_in(user, gameweek)

    fixture_a = make_finished_fixture(gameweek, "Team A", "Team B", 3, 0)
    db.session.commit()
    predict(dana, fixture_a, 3, 0)  # exact -> 16, but we'll pad further below
    predict(eve, fixture_a, 0, 0)   # wrong -> 0
    db.session.commit()
    score_finished_fixture(fixture_a)
    db.session.commit()

    # Pad Dana's lead further with a second already-scored fixture so even a
    # final-fixture exact score (16) can't let Eve catch up.
    fixture_b = make_finished_fixture(gameweek, "Team C", "Team D", 1, 0)
    db.session.commit()
    predict(dana, fixture_b, 1, 0)  # exact -> 16 (Dana: 16+16=32 so far)
    predict(eve, fixture_b, 0, 1)   # wrong -> 0  (Eve: 0 so far)
    db.session.commit()
    score_finished_fixture(fixture_b)
    db.session.commit()

    final_fixture = make_unfinished_fixture(gameweek, "Team E", "Team F")
    db.session.commit()
    predict(dana, final_fixture, 1, 1)
    predict(eve, final_fixture, 2, 0)
    db.session.commit()

    result = calculate_final_permutations(gameweek)
    print_result("SCENARIO B - already_won (leader's lead can't be caught)", result)


def scenario_outright_and_fallback():
    """A close two-player race: the trailing player only wins if the final
    fixture is their exact predicted scoreline (wins outright, single solo
    scenario) - and the leader wins in every OTHER scenario (the "more than
    one but not all" fallback summary). Current points: Frank 12 (2x
    correct-result), Grace 0 (2x wrong) - close enough that Grace's 16-point
    exact-score swing overtakes Frank, but her 6-point correct-result bucket
    swing doesn't.
    """
    from app.permutations import calculate_final_permutations

    gameweek = make_gameweek(3, "Gameweek 3")
    frank, grace = make_user("Frank"), make_user("Grace")
    db.session.commit()
    for user in (frank, grace):
        opt_in(user, gameweek)

    fixture_a = make_finished_fixture(gameweek, "Team A", "Team B", 1, 0)
    db.session.commit()
    predict(frank, fixture_a, 2, 0)  # correct result (home win), not exact -> 6
    predict(grace, fixture_a, 0, 1)  # wrong (predicted away win) -> 0
    db.session.commit()
    score_finished_fixture(fixture_a)
    db.session.commit()

    fixture_b = make_finished_fixture(gameweek, "Team C", "Team D", 0, 0)
    db.session.commit()
    predict(frank, fixture_b, 1, 1)  # correct result (draw), not exact -> 6 (Frank: 6+6=12)
    predict(grace, fixture_b, 2, 0)  # wrong (predicted home win) -> 0  (Grace: 0+0=0)
    db.session.commit()
    score_finished_fixture(fixture_b)
    db.session.commit()

    final_fixture = make_unfinished_fixture(gameweek, "Team E", "Team F")
    db.session.commit()
    predict(frank, final_fixture, 1, 1)  # draw - never matches a home-win scenario
    predict(grace, final_fixture, 3, 0)  # Grace's only hope: this exact scoreline
    db.session.commit()

    result = calculate_final_permutations(gameweek)
    print_result("SCENARIO C - wins-outright / needs-multiple-results fallback", result)


def main():
    app = build_test_app()
    with app.app_context():
        scenario_primary()
        scenario_already_won()
        scenario_outright_and_fallback()


if __name__ == "__main__":
    main()
