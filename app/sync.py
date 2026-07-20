"""Syncs fixtures and results from football-data.org into our database, and triggers scoring.

Gameweeks are auto-created from the API's own `matchday` field (1-38, known for the whole
season upfront) rather than hand-curated by the admin - see `_get_or_create_gameweek`.
"""

import logging
from datetime import datetime, timezone

from app.extensions import db
from app.football_data import get_premier_league_matches
from app.models import DEFAULT_STAKE_AMOUNT, Fixture, GAMEWEEK_STATUS_DRAFT, Gameweek
from app.scoring import score_fixture

logger = logging.getLogger(__name__)


def _parse_kickoff(utc_date: str) -> datetime:
    # football-data.org returns ISO 8601 with a trailing "Z"; store as naive UTC to match
    # the rest of the app's `datetime.utcnow()` usage.
    dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _get_or_create_gameweek(matchday):
    gameweek = Gameweek.query.filter_by(matchday=matchday).first()
    if gameweek is None:
        gameweek = Gameweek(
            matchday=matchday,
            name=f"Gameweek {matchday}",
            stake_amount=DEFAULT_STAKE_AMOUNT,
        )
        db.session.add(gameweek)
        db.session.flush()
    return gameweek


def sync_fixtures_and_results(season=None, date_from=None, date_to=None):
    """Upsert fixtures from the API and (re)score any that have finished.

    `date_from`/`date_to` (YYYY-MM-DD) narrow the API request - used by the live poller
    to cheaply re-fetch just today's matches rather than the whole season.

    Returns a summary dict: {"created": n, "updated": n, "scored_fixtures": n, "flagged_for_review": [...]}
    `flagged_for_review` lists fixtures where the API reported a different `matchday` than
    the gameweek they're currently assigned to, but that gameweek is already ACTIVE or
    COMPLETE - moving them automatically could invalidate predictions already locked in, so
    they're left in place for the admin to review and re-home manually if needed (see
    admin.assign_fixtures/unassign_fixture). Fixtures still in a DRAFT gameweek are moved
    automatically since nothing has been predicted against them yet.
    """
    matches = get_premier_league_matches(season=season, date_from=date_from, date_to=date_to)

    created = 0
    fixtures_updated = 0
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
        old_home_score = fixture.home_score
        old_away_score = fixture.away_score

        home_team = match["homeTeam"]
        away_team = match["awayTeam"]
        fixture.home_team = home_team.get("name") or home_team.get("shortName") or "TBD"
        fixture.away_team = away_team.get("name") or away_team.get("shortName") or "TBD"
        fixture.home_short_name = home_team.get("shortName") or home_team.get("name") or "TBD"
        fixture.away_short_name = away_team.get("shortName") or away_team.get("name") or "TBD"
        fixture.home_crest_url = home_team.get("crest")
        fixture.away_crest_url = away_team.get("crest")
        fixture.kickoff_at = _parse_kickoff(match["utcDate"])
        fixture.status = match.get("status", fixture.status)
        fixture.current_minute = match.get("minute")
        fixture.current_injury_time = match.get("injuryTime")
        fixture.last_synced_at = datetime.utcnow()

        matchday = match.get("matchday")
        if matchday is not None:
            current_gameweek = fixture.gameweek
            if current_gameweek is None or current_gameweek.matchday != matchday:
                if current_gameweek is not None and current_gameweek.status != GAMEWEEK_STATUS_DRAFT:
                    flagged_for_review.append(fixture)
                else:
                    fixture.gameweek = _get_or_create_gameweek(matchday)

        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}
        if full_time.get("home") is not None and full_time.get("away") is not None:
            if not fixture.manually_corrected:
                fixture.home_score = full_time["home"]
                fixture.away_score = full_time["away"]

        if not is_new:
            logger.info(
                "Sync: fixture %s (%s v %s) status %s -> %s, score %s-%s -> %s-%s",
                fixture.external_id, fixture.home_team, fixture.away_team,
                old_status, fixture.status,
                old_home_score, old_away_score, fixture.home_score, fixture.away_score,
            )

        if fixture.is_live:
            logger.info(
                "Sync: fixture %s (%s v %s) live - status=%s minute=%r injuryTime=%r (raw API minute=%r injuryTime=%r)",
                fixture.external_id, fixture.home_team, fixture.away_team,
                fixture.status, fixture.current_minute, fixture.current_injury_time,
                match.get("minute"), match.get("injuryTime"),
            )

        if is_new:
            created += 1
        else:
            fixtures_updated += 1
        touched_fixtures.append(fixture)

    db.session.flush()

    # Rescore against the current score on every tick a fixture has one - whether the
    # match is finished or still live, so points (and gameweek totals) update in real time
    # as the score changes during play, and finished fixtures get their final score.
    for fixture in touched_fixtures:
        if fixture.home_score is not None and fixture.away_score is not None:
            predictions_updated = score_fixture(fixture)
            if predictions_updated > 0:
                scored_fixtures += 1
            if fixture.is_live or fixture.is_finished:
                logger.info(
                    "Sync: rescored fixture %s (%s v %s) %s-%s (status=%s) -> %d prediction(s) updated",
                    fixture.external_id, fixture.home_team, fixture.away_team,
                    fixture.home_score, fixture.away_score, fixture.status, predictions_updated,
                )

    db.session.commit()

    return {
        "created": created,
        "updated": fixtures_updated,
        "scored_fixtures": scored_fixtures,
        "flagged_for_review": [f.id for f in flagged_for_review],
    }
