import os


def _normalize_db_url(url: str) -> str:
    """Normalize Railway/Heroku-style URLs for SQLAlchemy + psycopg v3.

    Those platforms hand out `postgres://...` URLs, but SQLAlchemy requires the
    `postgresql://` scheme. We also pin the driver to `+psycopg` (psycopg v3, installed
    as `psycopg[binary]`) explicitly - the bare `postgresql://` scheme resolves to
    psycopg2 by default, which fails on Railway with `ImportError: libpq.so.5: cannot
    open shared object file` because it dynamically links against a system libpq that
    isn't present in the runtime image. psycopg v3's binary wheel bundles libpq instead.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(os.getcwd(), "app.db"))
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # football-data.org API
    FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
    FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
    WORLD_CUP_COMPETITION_CODE = "WC"

    # Superuser, created/updated automatically on startup via `flask seed-admin`
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

    # Scoring
    POINTS_CORRECT_RESULT = 6
    POINTS_EXACT_SCORE = 16

    # Live polling window: from N minutes before the day's earliest kick-off to
    # (assumed match duration + M minutes) after the day's latest kick-off.
    LIVE_POLL_PRE_KICKOFF_MINUTES = 15
    LIVE_POLL_POST_FINAL_MINUTES = 30
    LIVE_POLL_ASSUMED_MATCH_MINUTES = 105  # 90 minutes + stoppage time/breaks, per spec
    LIVE_POLL_INTERVAL_SECONDS = 30

    # Outside live windows, a lightweight daily sync runs once at this UTC hour.
    DAILY_SYNC_HOUR_UTC = 6
    DAILY_SYNC_MINUTE_UTC = 0

    # Set to "false" to disable the background scheduler entirely (e.g. for one-off scripts/tests).
    ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "true").lower() == "true"

    # Prediction lock window
    PREDICTION_LOCK_MINUTES_BEFORE_KICKOFF = 5
