import os

import click
from flask import Flask

from app.extensions import csrf, db, login_manager, migrate
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

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
    maybe_start_scheduler(app)

    return app


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
        user = User(username=username, email=email.lower())
        db.session.add(user)

    user.username = username
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


def register_template_helpers(app):
    from datetime import datetime

    @app.context_processor
    def inject_now():
        return {"current_year": datetime.utcnow().year}
