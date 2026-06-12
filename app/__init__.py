import logging
import os
import time

import click
from flask import Flask

from app.extensions import csrf, db, login_manager, migrate
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Root logger defaults to WARNING, which would silently drop the INFO-level
    # sync/scheduler/poll logging (app.sync, app.scheduler, etc. - all children of
    # this "app" logger) that the live-score polling relies on for diagnostics.
    app.logger.setLevel(logging.INFO)
    # Stamped once at process startup, injected into /sw.js so every deploy (= process
    # restart) produces different SW bytes → browser detects the change automatically.
    app.config["DEPLOY_TIME"] = int(time.time())
    app.config["APP_VERSION"] = _read_version()

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (registers models with SQLAlchemy)

    @login_manager.user_loader
    def load_user(user_id):
        return models.User.query.get(int(user_id))

    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.main import bp as main_bp
    from app.blueprints.predictions import bp as predictions_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(predictions_bp)
    app.register_blueprint(admin_bp)

    register_cli(app)
    register_template_helpers(app)
    run_startup_tasks(app)
    _reenable_app_loggers()
    maybe_start_scheduler(app)

    return app


def _reenable_app_loggers():
    """Undo Alembic's `disable_existing_loggers` fallout from `run_startup_tasks`.

    `flask_migrate.upgrade()` runs Alembic's `fileConfig`, which (by its default
    `disable_existing_loggers=True`) disables every logger that already exists at
    that point - including `app.sync`/`app.scheduler`, since those modules are
    imported via blueprint registration before migrations run. Without this, their
    live-poll diagnostic logging is silently dropped for the lifetime of the process.
    """
    for name, logger in logging.Logger.manager.loggerDict.items():
        if isinstance(logger, logging.Logger) and (name == "app" or name.startswith("app.")):
            logger.disabled = False


def _read_version():
    """Read the app version from the VERSION file at the project root.

    Single source of truth for the version shown on the About page - bump
    that file alone for future releases.
    """
    version_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def run_startup_tasks(app):
    """Run the setup that Railway's Procfile `release:` step was supposed to handle.

    Railway does not execute Heroku-style `release:` lines, so on Railway
    `flask db upgrade && flask seed-admin` never ran - leaving the schema unmigrated
    (causing "relation does not exist" errors on first queries, e.g. the scheduler's
    fixtures lookup) and no admin account (causing login to 500). Running both here,
    on every boot, makes deploys self-sufficient regardless of platform. Both are
    idempotent - `upgrade()` only applies pending migrations, `_seed_admin` upserts by
    email - and each is wrapped so a failure (e.g. DB not reachable yet) is logged
    rather than crashing the whole app.
    """
    with app.app_context():
        try:
            from flask_migrate import upgrade

            upgrade()
        except Exception:
            app.logger.exception("Startup: failed to run database migrations")
            return

        try:
            _seed_admin(app)
        except Exception:
            app.logger.exception("Startup: failed to seed admin user")


def _seed_admin(app):
    """Create or promote the superuser account from ADMIN_* environment variables.

    Returns `(user, created)`, or `(None, None)` if the ADMIN_* vars aren't all set.
    Shared by the `seed-admin` CLI command and the startup fallback in `run_startup_tasks`,
    so the two can never drift out of sync.
    """
    username = app.config["ADMIN_USERNAME"]
    email = app.config["ADMIN_EMAIL"]
    password = app.config["ADMIN_PASSWORD"]

    if not (username and email and password):
        return None, None

    from app.extensions import db
    from app.models import User

    user = User.query.filter_by(email=email.lower()).first()
    created = user is None
    if user is None:
        user = User(username=username, display_name=username, email=email.lower())
        db.session.add(user)

    user.username = username
    if not user.display_name:
        user.display_name = username
    user.is_admin = True
    user.set_password(password)
    db.session.commit()
    return user, created


def maybe_start_scheduler(app):
    """Start the background poller, but not in the Flask reloader's throwaway parent process.

    `flask run` with the reloader spawns a parent (which only watches for file changes -
    `WERKZEUG_RUN_MAIN` unset) and a child that actually serves requests (`WERKZEUG_RUN_MAIN=true`).
    Starting the scheduler in both would poll the API twice as often. In production
    (gunicorn, `app.debug` False) there's no reloader, so it starts immediately.
    """
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    from app.scheduler import init_scheduler

    init_scheduler(app)


def register_cli(app):
    @app.cli.command("seed-admin")
    def seed_admin():
        """Create or promote the superuser account from ADMIN_* environment variables."""
        user, created = _seed_admin(app)
        if user is None:
            click.echo("ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD must all be set.")
            return

        verb = "Creating" if created else "Promoting existing"
        click.echo(f"{verb} superuser '{user.username}' <{user.email}>.")
        click.echo("Done.")

    @app.cli.command("seed-test-data")
    def seed_test_data():
        """Seed two opted-in test players with predictions and a round in progress.

        For local/dev use against the active round only - creates/updates "Alice" and
        "Bruno" (password "test123"), opts them into the active round, marks its first
        three fixtures as finished (2-1 home win, 0-0 draw, 1-2 away win), gives both
        users a prediction for every fixture (Alice's first three match the results
        exactly; Bruno's are 2-1/1-1/1-0; remaining fixtures get a random 0-3 score for
        both), and rescores so points show up in the players grid. Idempotent - safe to
        run again to re-roll the random predictions for the remaining fixtures.
        """
        if os.environ.get("FLASK_ENV") == "production":
            click.echo("Refusing to run: FLASK_ENV=production.")
            return

        import random

        from app.extensions import db
        from app.models import OUTCOME_AWAY, OUTCOME_DRAW, OUTCOME_HOME, Prediction, RoundEntry, User
        from app.round_helpers import get_active_round
        from app.scoring import score_fixture

        round_ = get_active_round()
        if round_ is None:
            click.echo("No active round - nothing to seed.")
            return

        users = {}
        for username, display_name, email in (
            ("alice_test", "Alice", "alice.test@example.com"),
            ("bruno_test", "Bruno", "bruno.test@example.com"),
        ):
            user = User.query.filter_by(username=username).first()
            if user is None:
                user = User(username=username, display_name=display_name, email=email)
                db.session.add(user)
            user.display_name = display_name
            user.email = email
            user.set_password("test123")
            users[display_name] = user
        db.session.flush()

        for user in users.values():
            entry = RoundEntry.query.filter_by(user_id=user.id, round_id=round_.id).first()
            if entry is None:
                entry = RoundEntry(user_id=user.id, round_id=round_.id, opted_in=True)
                db.session.add(entry)
            else:
                entry.opted_in = True

        fixtures = round_.fixtures.all()

        # Results for the first three fixtures, simulating a round in progress.
        RESULTS = [(2, 1), (0, 0), (1, 2)]
        # Alice's predictions match the first three results exactly (max points);
        # Bruno's are close but only nail the first one.
        FIXED_PREDICTIONS = {
            "Alice": [(2, 1), (0, 0), (1, 2)],
            "Bruno": [(2, 1), (1, 1), (1, 0)],
        }

        for display_name, user in users.items():
            fixed = FIXED_PREDICTIONS[display_name]
            for index, fixture in enumerate(fixtures):
                prediction = Prediction.query.filter_by(user_id=user.id, fixture_id=fixture.id).first()
                if prediction is None:
                    prediction = Prediction(user_id=user.id, fixture_id=fixture.id, predicted_home=0, predicted_away=0)
                    db.session.add(prediction)
                if index < len(fixed):
                    prediction.predicted_home, prediction.predicted_away = fixed[index]
                else:
                    prediction.predicted_home = random.randint(0, 3)
                    prediction.predicted_away = random.randint(0, 3)

        db.session.commit()

        click.echo(f"Seeded users: {', '.join(users.keys())} (password: test123)")
        click.echo(f"Opted both into '{round_.name}' and seeded {len(fixtures)} prediction(s) each.")

        if not fixtures:
            click.echo(f"{round_.name} has no fixtures - nothing to score.")
            return

        # Mark the first len(RESULTS) fixtures (by kickoff) as finished with the scores above.
        total_updated = 0
        for fixture, (home_score, away_score) in zip(fixtures, RESULTS):
            fixture.home_score_90 = home_score
            fixture.away_score_90 = away_score
            fixture.status = "FINISHED"
            if home_score > away_score:
                fixture.winner = OUTCOME_HOME
            elif away_score > home_score:
                fixture.winner = OUTCOME_AWAY
            else:
                fixture.winner = OUTCOME_DRAW
        db.session.commit()

        for fixture, _ in zip(fixtures, RESULTS):
            total_updated += score_fixture(fixture)
        db.session.commit()

        for fixture, _ in zip(fixtures, RESULTS):
            click.echo(
                f"Marked '{fixture.home_team} {fixture.home_score_90}-{fixture.away_score_90} "
                f"{fixture.away_team}' as finished."
            )
        click.echo(f"Rescored {total_updated} prediction(s) across {min(len(fixtures), len(RESULTS))} fixture(s).")

    @app.cli.command("reset-dev")
    def reset_dev():
        """Wipe the dev database back to a clean slate: no rounds, no players, no predictions.

        Deletes all predictions, round entries, rounds, and non-admin users, and resets
        every fixture's score/status back to a not-yet-played state - then re-seeds the
        admin account. Refuses to run with FLASK_ENV=production.
        """
        if os.environ.get("FLASK_ENV") == "production":
            click.echo("Refusing to run: FLASK_ENV=production.")
            return

        from app.extensions import db
        from app.models import Fixture, Prediction, Round, RoundEntry, User

        predictions_count = Prediction.query.delete()
        round_entries_count = RoundEntry.query.delete()

        # Fixtures reference rounds via round_id - clear that (and flush) before
        # deleting rounds, or the DELETE will violate the foreign key constraint.
        fixtures = Fixture.query.all()
        for fixture in fixtures:
            fixture.round_id = None
            fixture.home_score_90 = None
            fixture.away_score_90 = None
            fixture.winner = None
            fixture.status = "TIMED"
            fixture.last_synced_at = None
        db.session.flush()

        rounds_count = Round.query.delete()
        users_count = User.query.filter_by(is_admin=False).delete()

        db.session.commit()

        user, created = _seed_admin(app)

        click.echo("Reset dev database:")
        click.echo(f"  Deleted {predictions_count} prediction(s).")
        click.echo(f"  Deleted {round_entries_count} round entry/entries.")
        click.echo(f"  Deleted {rounds_count} round(s).")
        click.echo(f"  Deleted {users_count} non-admin user(s).")
        click.echo(f"  Reset {len(fixtures)} fixture(s) to TIMED with scores cleared and unassigned from rounds.")
        if user is None:
            click.echo("  ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD must all be set - admin user not seeded.")
        else:
            verb = "Created" if created else "Verified"
            click.echo(f"  {verb} admin user '{user.username}' <{user.email}>.")
        click.echo("Done.")


def register_template_helpers(app):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    UTC = ZoneInfo("UTC")
    LONDON = ZoneInfo("Europe/London")

    @app.context_processor
    def inject_now():
        return {"current_year": datetime.utcnow().year, "app_version": app.config["APP_VERSION"]}

    @app.template_filter("gbp")
    def format_gbp(amount, signed=False):
        """Render a Decimal/£ amount as e.g. '£5.00' or, when `signed`, '+£5.00'/'-£5.00'."""
        sign = "-" if amount < 0 else ("+" if signed else "")
        return f"{sign}£{abs(amount):.2f}"

    @app.template_filter("flag")
    def format_team_flag(team_name):
        """Render a national team's flag emoji, or '' if the team isn't recognised."""
        from app.teams import flag_for

        return flag_for(team_name)

    @app.template_filter("london")
    def format_london_time(value, fmt="%a %d %b, %H:%M %Z"):
        """Render a naive UTC datetime (as stored in the DB) in UK local time.

        Converts via Europe/London so kick-offs display correctly whether the
        UK is on GMT or BST (e.g. the 2026 World Cup runs during BST) - the
        `%Z` in the default format then renders the right abbreviation for the
        date in question, rather than a hard-coded "UTC"/"BST" label.
        """
        if value is None:
            return ""
        return value.replace(tzinfo=UTC).astimezone(LONDON).strftime(fmt)
