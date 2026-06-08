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
    maybe_start_scheduler(app)

    return app


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
        username = app.config["ADMIN_USERNAME"]
        email = app.config["ADMIN_EMAIL"]
        password = app.config["ADMIN_PASSWORD"]

        if not (username and email and password):
            click.echo("ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD must all be set.")
            return

        from app.extensions import db
        from app.models import User

        user = User.query.filter_by(email=email.lower()).first()
        if user is None:
            user = User(username=username, email=email.lower())
            db.session.add(user)
            click.echo(f"Creating superuser '{username}' <{email}>.")
        else:
            click.echo(f"Promoting existing user '{user.username}' <{user.email}> to superuser.")

        user.username = username
        user.is_admin = True
        user.set_password(password)
        db.session.commit()
        click.echo("Done.")


def register_template_helpers(app):
    from datetime import datetime

    @app.context_processor
    def inject_now():
        return {"current_year": datetime.utcnow().year}
