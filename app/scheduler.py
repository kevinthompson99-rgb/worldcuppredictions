"""Background polling for fixtures/results, per the spec's polling rules:

  - During a "live match window" - 15 minutes before the day's earliest kick-off
    through (assumed match length + 30 minutes) after the day's latest kick-off -
    poll the API every 3 minutes for that day's matches only.
  - Outside live windows, run one lightweight full sync per day at 06:00 UTC to
    pick up fixture changes and newly confirmed knockout matchups.

Implemented with APScheduler's BackgroundScheduler running inside the Flask process:
one job ticks every 3 minutes and only does work if "now" falls in today's live
window; a second job runs on a daily cron trigger. Every run - live or daily, and
whether or not it found anything - is recorded in `PollLog` so the admin panel can
show when polling last ran and what it found.

NOTE: this assumes a single process polls (see Procfile - gunicorn runs with one
worker). Running multiple workers/dynos would each start their own scheduler and
poll redundantly; if you ever need to scale web workers, gate this behind a single
dedicated worker process or an external scheduler instead.
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.extensions import db
from app.models import Fixture, PollLog
from app.sync import sync_fixtures_and_results

logger = logging.getLogger(__name__)

_scheduler = None


def _todays_fixtures(app, now):
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    return Fixture.query.filter(
        Fixture.kickoff_at >= start_of_day, Fixture.kickoff_at < end_of_day
    ).all()


def get_live_window(app, now=None):
    """Return (start, end) for today's live match window, or None if no matches today."""
    now = now or datetime.utcnow()
    fixtures = _todays_fixtures(app, now)
    if not fixtures:
        return None

    earliest = min(f.kickoff_at for f in fixtures)
    latest = max(f.kickoff_at for f in fixtures)
    start = earliest - timedelta(minutes=app.config["LIVE_POLL_PRE_KICKOFF_MINUTES"])
    end = latest + timedelta(
        minutes=app.config["LIVE_POLL_ASSUMED_MATCH_MINUTES"] + app.config["LIVE_POLL_POST_FINAL_MINUTES"]
    )
    return start, end


def _record_poll(mode, summary=None, error=None):
    log = PollLog(mode=mode)
    if error is not None:
        log.succeeded = False
        log.detail = error
    else:
        log.succeeded = True
        log.fixtures_created = summary["created"]
        log.fixtures_updated = summary["updated"]
        log.fixtures_scored = summary["scored_fixtures"]
        if summary["flagged_for_review"]:
            log.detail = (
                "Went to extra time/penalties - verify 90-minute score: "
                f"fixture id(s) {summary['flagged_for_review']}"
            )
    db.session.add(log)
    db.session.commit()


def _run_live_poll(app):
    with app.app_context():
        now = datetime.utcnow()
        window = get_live_window(app, now)
        in_window = window is not None and window[0] <= now <= window[1]

        if window is not None:
            logger.info(
                "Live poll tick: now=%s UTC, window=%s..%s UTC, in_window=%s",
                now.isoformat(), window[0].isoformat(), window[1].isoformat(), in_window,
            )
        else:
            logger.info("Live poll tick: now=%s UTC, no fixtures today, in_window=False", now.isoformat())

        if not in_window:
            return

        logger.info("Live poll: in today's match window, fetching today's results")
        today = now.strftime("%Y-%m-%d")
        try:
            summary = sync_fixtures_and_results(date_from=today, date_to=today)
        except Exception as exc:
            logger.exception("Live poll failed")
            _record_poll("live", error=str(exc))
            return

        logger.info(
            "Live poll done: %d created, %d updated, %d (re)scored",
            summary["created"], summary["updated"], summary["scored_fixtures"],
        )
        _record_poll("live", summary=summary)


def _run_daily_sync(app):
    with app.app_context():
        logger.info("Daily sync: fetching full fixture list")
        try:
            summary = sync_fixtures_and_results()
        except Exception as exc:
            logger.exception("Daily sync failed")
            _record_poll("daily", error=str(exc))
            return

        logger.info(
            "Daily sync done: %d created, %d updated, %d (re)scored",
            summary["created"], summary["updated"], summary["scored_fixtures"],
        )
        _record_poll("daily", summary=summary)


def init_scheduler(app):
    """Start the background scheduler. Safe to call multiple times - only starts once."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    if not app.config["ENABLE_SCHEDULER"]:
        logger.info("Scheduler disabled via ENABLE_SCHEDULER=false")
        return None

    if not app.config.get("FOOTBALL_DATA_API_KEY"):
        logger.warning("FOOTBALL_DATA_API_KEY not set - scheduler will not start")
        return None

    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(
        func=_run_live_poll,
        args=[app],
        trigger="interval",
        minutes=app.config["LIVE_POLL_INTERVAL_MINUTES"],
        id="live_poll",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        func=_run_daily_sync,
        args=[app],
        trigger="cron",
        hour=app.config["DAILY_SYNC_HOUR_UTC"],
        minute=app.config["DAILY_SYNC_MINUTE_UTC"],
        id="daily_sync",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: live poll every %d min (active only during match windows), "
        "daily sync at %02d:%02d UTC",
        app.config["LIVE_POLL_INTERVAL_MINUTES"],
        app.config["DAILY_SYNC_HOUR_UTC"],
        app.config["DAILY_SYNC_MINUTE_UTC"],
    )
    return scheduler
