"""Syncs fixtures and results from football-data.org into our database, and triggers scoring.

KNOWN DATA GAP (see NOTES.md): the API's `score.fullTime` reflects the score at the end
of the match *as played* - for knockout matches that go to extra time this includes ET
goals, not the 90-minute score we need for scoring predictions. We store `fullTime` as
`home_score_90`/`away_score_90` by default (correct for the vast majority of matches that
are decided in regulation) and flag any fixture where `score.duration != REGULAR` so the
admin can manually correct the 90-minute score from the admin panel.
"""

import logging
from datetime import datetime, timezone

from app.extensions import db
from app.football_data import get_world_cup_matches
from app.models import Fixture, OUTCOME_AWAY, OUTCOME_DRAW, OUTCOME_HOME
from app.scoring import score_fixture

logger = logging.getLogger(__name__)

_API_WINNER_TO_OUTCOME = {
    "HOME_TEAM": OUTCOME_HOME,
    "AWAY_TEAM": OUTCOME_AWAY,
    "DRAW": OUTCOME_DRAW,
}

# Knockout stages per football-data.org's `stage` field - group stage fixtures never
# go to extra time, so anything else is treated as knockout for scoring purposes.
_KNOCKOUT_STAGES = {
    "LAST_16",
    "ROUND_OF_16",
    "QUARTER_FINALS",
    "SEMI_FINALS",
    "THIRD_PLACE",
    "FINAL",
}


def _parse_kickoff(utc_date: str) -> datetime:
    # football-data.org returns ISO 8601 with a trailing "Z"; store as naive UTC to match
    # the rest of the app's `datetime.utcnow()` usage.
    dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def sync_fixtures_and_results(date_from=None, date_to=None):
    """Upsert fixtures from the API and (re)score any that have finished.

    `date_from`/`date_to` (YYYY-MM-DD) narrow the API request - used by the live poller
    to cheaply re-fetch just today's matches rather than the whole tournament.

    Returns a summary dict: {"created": n, "updated": n, "scored_fixtures": n, "flagged_for_review": [...]}
    New fixtures are created without a round assignment - the admin assigns them manually,
    which is required for the knockout stage where matchups aren't known in advance.
    """
    matches = get_world_cup_matches(date_from=date_from, date_to=date_to)

    created = 0
    updated = 0
    scored_fixtures = 0
    flagged_for_review = []
    touched_fixtures = []

    for match in matches:
        external_id = match["id"]
        fixture = Fixture.query.filter_by(external_id=external_id).first()
        is_new = fixture is None
        if is_new:
            fixture = Fixture(external_id=external_id)
            db.session.add(fixture)

        old_status = fixture.status
        old_home_score = fixture.home_score_90
        old_away_score = fixture.away_score_90

        fixture.home_team = match["homeTeam"].get("name") or match["homeTeam"].get("shortName") or "TBD"
        fixture.away_team = match["awayTeam"].get("name") or match["awayTeam"].get("shortName") or "TBD"
        fixture.home_short_name = match["homeTeam"].get("shortName") or match["homeTeam"].get("name") or "TBD"
        fixture.away_short_name = match["awayTeam"].get("shortName") or match["awayTeam"].get("name") or "TBD"
        fixture.stage = match.get("stage")
        fixture.group_name = match.get("group")
        fixture.kickoff_at = _parse_kickoff(match["utcDate"])
        fixture.status = match.get("status", fixture.status)
        fixture.current_minute = match.get("minute")
        fixture.current_injury_time = match.get("injuryTime")
        fixture.is_knockout = fixture.stage in _KNOCKOUT_STAGES
        fixture.last_synced_at = datetime.utcnow()

        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}
        if full_time.get("home") is not None and full_time.get("away") is not None:
            fixture.home_score_90 = full_time["home"]
            fixture.away_score_90 = full_time["away"]
            fixture.winner = _API_WINNER_TO_OUTCOME.get(score.get("winner"))

            if score.get("duration") and score["duration"] != "REGULAR":
                flagged_for_review.append(fixture)

        if not is_new:
            logger.info(
                "Sync: fixture %s (%s v %s) status %s -> %s, score %s-%s -> %s-%s",
                fixture.external_id, fixture.home_team, fixture.away_team,
                old_status, fixture.status,
                old_home_score, old_away_score, fixture.home_score_90, fixture.away_score_90,
            )

        if is_new:
            created += 1
        else:
            updated += 1
        touched_fixtures.append(fixture)

    db.session.flush()

    # Rescore against the current score on every tick a fixture has one - whether the
    # match is finished or still live, so points (and round totals) update in real time
    # as the score changes during play, and finished fixtures get their final score.
    for fixture in touched_fixtures:
        if fixture.home_score_90 is not None and fixture.away_score_90 is not None:
            if score_fixture(fixture) > 0:
                scored_fixtures += 1

    db.session.commit()

    return {
        "created": created,
        "updated": updated,
        "scored_fixtures": scored_fixtures,
        "flagged_for_review": [f.id for f in flagged_for_review],
    }
