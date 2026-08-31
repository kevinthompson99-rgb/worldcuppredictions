from datetime import datetime, timedelta
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

# Match outcome constants used for both `Fixture.result_outcome` and prediction comparisons.
OUTCOME_HOME = "HOME"
OUTCOME_AWAY = "AWAY"
OUTCOME_DRAW = "DRAW"

# Gameweek lifecycle - fixtures/gameweeks are auto-populated from the football-data.org API
# (see app/sync.py), but the admin still explicitly controls visibility via this status, see
# app/gameweek_helpers.py and the admin blueprint's publish/complete actions.
#   DRAFT    - auto-created as fixtures sync in; invisible to users. Most of the season's
#              38 gameweeks legitimately sit here at any given time (no cap - the admin
#              publishes each one when ready, not all at once).
#   ACTIVE   - the one gameweek currently open to/visible by users; predictions + live results.
#   COMPLETE - locked, settled, and archived for reference (leaderboards/history).
GAMEWEEK_STATUS_DRAFT = "DRAFT"
GAMEWEEK_STATUS_ACTIVE = "ACTIVE"
GAMEWEEK_STATUS_COMPLETE = "COMPLETE"
GAMEWEEK_STATUSES = (GAMEWEEK_STATUS_DRAFT, GAMEWEEK_STATUS_ACTIVE, GAMEWEEK_STATUS_COMPLETE)

PREDICTION_LOCK_MINUTES_BEFORE_KICKOFF = 5

# Used as a gameweek's stake when sync auto-creates it - admin can override via
# admin.set_gameweek_stake.
DEFAULT_STAKE_AMOUNT = Decimal("5.00")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    # The public-facing name shown in the players grid, leaderboard, and pot/finance
    # views — kept separate from `username` so the login identity stays private.
    # Set at registration (or by the admin when creating an account) and editable
    # any time via auth.profile.
    display_name = db.Column(db.String(64), nullable=False)
    # Optional and unconstrained — only the seeded admin account has one (see
    # app/__init__.py:_seed_admin); regular sign-up only collects username/password.
    email = db.Column(db.String(120), unique=False, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # cascade="all, delete-orphan": deleting a user (admin.delete_user) must take
    # their predictions and gameweek entries (opt-ins included - opted_in lives on
    # GameweekEntry) with them, or the delete fails on the FK / leaves orphans that
    # keep them showing up in the players grid and standings.
    predictions = db.relationship("Prediction", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    push_subscriptions = db.relationship("PushSubscription", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        # Explicit method: some platforms' Python builds lack hashlib.scrypt (werkzeug's default).
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class Gameweek(db.Model):
    __tablename__ = "gameweeks"

    id = db.Column(db.Integer, primary_key=True)
    # football-data.org's own matchday number (1-38) - the season's fixture list is known
    # entirely upfront, so this (not an admin-assigned sequence) is the natural ordering key
    # and sync.py get-or-creates a Gameweek per distinct matchday it sees.
    matchday = db.Column(db.Integer, unique=True, nullable=False, index=True)
    # Auto-derived as "Gameweek {matchday}" when sync creates the row.
    name = db.Column(db.String(120), nullable=False)
    # Per-gameweek buy-in - sized for GBP to the penny. Defaults to DEFAULT_STAKE_AMOUNT when
    # sync auto-creates the gameweek; admin can override any time via admin.set_gameweek_stake.
    stake_amount = db.Column(db.Numeric(8, 2), nullable=False, default=DEFAULT_STAKE_AMOUNT)
    status = db.Column(db.String(16), nullable=False, default=GAMEWEEK_STATUS_DRAFT, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Dev/testing escape hatch: lets an admin lock a gameweek on demand, bypassing the
    # kick-off-based lock_time below. See admin.force_lock_gameweek.
    force_locked = db.Column(db.Boolean, nullable=False, default=False)

    # Set once the 24-hour/1-hour-to-deadline push notification has been sent for this
    # gameweek (see app/scheduler.py) - guards against re-sending on every poll tick
    # while lock_time still falls inside that job's window.
    notified_24h = db.Column(db.Boolean, nullable=False, default=False)
    notified_1h = db.Column(db.Boolean, nullable=False, default=False)

    fixtures = db.relationship(
        "Fixture",
        back_populates="gameweek",
        order_by="Fixture.kickoff_at",
        lazy="dynamic",
    )

    @property
    def earliest_kickoff(self):
        first = self.fixtures.order_by(Fixture.kickoff_at.asc()).first()
        return first.kickoff_at if first else None

    @property
    def latest_final_whistle(self):
        """Approximate end of the last match (kickoff + 2 hours)."""
        last = self.fixtures.order_by(Fixture.kickoff_at.desc()).first()
        if not last:
            return None
        return last.kickoff_at + timedelta(hours=2)

    @property
    def lock_time(self):
        """Predictions for the gameweek lock 5 minutes before its earliest kick-off."""
        earliest = self.earliest_kickoff
        if earliest is None:
            return None
        return earliest - timedelta(minutes=PREDICTION_LOCK_MINUTES_BEFORE_KICKOFF)

    @property
    def is_locked(self):
        if self.force_locked:
            return True
        lock_time = self.lock_time
        return lock_time is not None and datetime.utcnow() >= lock_time

    @property
    def all_fixtures_settled(self):
        """Locked, with fixtures assigned, and every one of them finished and scored.

        This is a computed *readiness* check, distinct from `status == COMPLETE` (which
        is an explicit admin decision to archive the gameweek - see admin.complete_gameweek).
        It's used to inform the admin when a gameweek looks ready to be marked complete.
        """
        fixtures = self.fixtures.all()
        return bool(fixtures) and self.is_locked and all(f.is_finished for f in fixtures)

    def __repr__(self):
        return f"<Gameweek {self.matchday}: {self.name} [{self.status}]>"


class Fixture(db.Model):
    __tablename__ = "fixtures"

    id = db.Column(db.Integer, primary_key=True)
    # football-data.org match id - lets us reconcile synced fixtures/results.
    external_id = db.Column(db.Integer, unique=True, nullable=True, index=True)

    gameweek_id = db.Column(db.Integer, db.ForeignKey("gameweeks.id"), nullable=True, index=True)
    gameweek = db.relationship("Gameweek", back_populates="fixtures")

    home_team = db.Column(db.String(120), nullable=False)
    away_team = db.Column(db.String(120), nullable=False)
    home_short_name = db.Column(db.String(80), nullable=True)
    away_short_name = db.Column(db.String(80), nullable=True)

    # Club crest image URLs, straight from the API's match payload - no separate teams
    # lookup/model needed.
    home_crest_url = db.Column(db.String(255), nullable=True)
    away_crest_url = db.Column(db.String(255), nullable=True)

    kickoff_at = db.Column(db.DateTime, nullable=False, index=True)

    # football-data.org status, e.g. SCHEDULED / TIMED / IN_PLAY / PAUSED / FINISHED / POSTPONED.
    status = db.Column(db.String(32), nullable=False, default="SCHEDULED")

    # Full-time score - what predictions are scored against. League matches are always
    # decided at 90 minutes (plus stoppage time), so there's no separate "90-minute vs.
    # extra-time" distinction to track here.
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)

    # Live match clock from the API ("minute"/"injuryTime"), only meaningful while
    # status is one of _LIVE_MINUTE_STATUSES - see minute_display.
    current_minute = db.Column(db.Integer, nullable=True)
    current_injury_time = db.Column(db.Integer, nullable=True)

    last_synced_at = db.Column(db.DateTime, nullable=True)
    manually_corrected = db.Column(db.Boolean, nullable=False, default=False)

    predictions = db.relationship("Prediction", back_populates="fixture", lazy="dynamic")

    # football-data.org statuses, used to drive the live-score display (see
    # main.players / main.scores_live and templates/main/players.html).
    _FINISHED_STATUSES = ("FINISHED", "AWARDED")
    _LIVE_MINUTE_STATUSES = ("IN_PLAY", "SUSPENDED")
    _IN_PROGRESS_STATUSES = ("IN_PLAY", "PAUSED", "SUSPENDED")

    @property
    def is_finished(self):
        """FINISHED or AWARDED - the result is final and "FT" is shown for the rest of the gameweek."""
        return self.status in self._FINISHED_STATUSES

    @property
    def is_live(self):
        """IN_PLAY, PAUSED or SUSPENDED - a match currently underway (incl. half-time/stoppages)."""
        return self.status in self._IN_PROGRESS_STATUSES

    @property
    def is_live_minute(self):
        """IN_PLAY or SUSPENDED - statuses where the API's match-minute clock applies."""
        return self.status in self._LIVE_MINUTE_STATUSES

    @property
    def minute_display(self):
        """Match clock as shown to users, e.g. "41'" or "45+2'" during injury time."""
        if self.current_minute is None:
            return None
        if self.current_injury_time:
            return f"{self.current_minute}+{self.current_injury_time}'"
        return f"{self.current_minute}'"

    @property
    def result_outcome(self):
        """HOME/AWAY/DRAW based on the current/full-time score - used for scoring."""
        if self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return OUTCOME_HOME
        if self.away_score > self.home_score:
            return OUTCOME_AWAY
        return OUTCOME_DRAW

    def __repr__(self):
        return f"<Fixture {self.home_team} v {self.away_team} @ {self.kickoff_at}>"


class GameweekEntry(db.Model):
    """A user's opt-in/out decision for a gameweek's £5 pot (see app/finance.py).

    Opting in is financial only - it doesn't gate prediction *visibility*, but the app
    only lets opted-in users submit predictions and only shows opted-in users in the
    players grid. A row is created on the user's first toggle and then flipped in place,
    so `opted_in` always reflects their latest choice (allowed any time up to lock).
    """

    __tablename__ = "gameweek_entries"
    __table_args__ = (
        db.UniqueConstraint("user_id", "gameweek_id", name="uq_gameweek_entry_user_gameweek"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    gameweek_id = db.Column(db.Integer, db.ForeignKey("gameweeks.id"), nullable=False, index=True)
    opted_in = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("gameweek_entries", lazy="dynamic", cascade="all, delete-orphan"))
    gameweek = db.relationship("Gameweek", backref=db.backref("entries", lazy="dynamic"))

    def __repr__(self):
        return f"<GameweekEntry user={self.user_id} gameweek={self.gameweek_id} opted_in={self.opted_in}>"


class Prediction(db.Model):
    __tablename__ = "predictions"
    __table_args__ = (
        db.UniqueConstraint("user_id", "fixture_id", name="uq_prediction_user_fixture"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    fixture_id = db.Column(db.Integer, db.ForeignKey("fixtures.id"), nullable=False, index=True)

    predicted_home = db.Column(db.Integer, nullable=False)
    predicted_away = db.Column(db.Integer, nullable=False)

    # Populated once the fixture finishes and scoring runs.
    points = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", back_populates="predictions")
    fixture = db.relationship("Fixture", back_populates="predictions")

    @property
    def predicted_outcome(self):
        if self.predicted_home > self.predicted_away:
            return OUTCOME_HOME
        if self.predicted_away > self.predicted_home:
            return OUTCOME_AWAY
        return OUTCOME_DRAW

    def __repr__(self):
        return f"<Prediction user={self.user_id} fixture={self.fixture_id} {self.predicted_home}-{self.predicted_away}>"


class PollLog(db.Model):
    """Record of each results-sync run (scheduled or manually triggered), for the admin panel."""

    __tablename__ = "poll_logs"

    id = db.Column(db.Integer, primary_key=True)
    run_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    # "live" (30-second polling during a match window), "daily" (06:00 UTC sync), or "manual" (admin-triggered).
    mode = db.Column(db.String(16), nullable=False)
    succeeded = db.Column(db.Boolean, nullable=False, default=True)
    fixtures_created = db.Column(db.Integer, nullable=False, default=0)
    fixtures_updated = db.Column(db.Integer, nullable=False, default=0)
    fixtures_scored = db.Column(db.Integer, nullable=False, default=0)
    # Error message on failure, or notes such as fixtures flagged because the API reported a
    # different matchday than the one they're currently (already published) assigned to.
    detail = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<PollLog {self.mode} @ {self.run_at} ok={self.succeeded} created={self.fixtures_created} updated={self.fixtures_updated}>"


class PushSubscription(db.Model):
    """A browser's Web Push subscription (see app/push.py), one row per device/browser
    a user has enabled notifications on. `endpoint` is the push service URL the browser
    handed back from `pushManager.subscribe()` - unique per subscription, so re-subscribing
    the same device (e.g. after clearing the permission) just updates the existing row.
    """

    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="push_subscriptions")

    def __repr__(self):
        return f"<PushSubscription user={self.user_id} endpoint={self.endpoint[:40]}...>"
