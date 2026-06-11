from datetime import datetime, timedelta
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

# Match outcome constants used for both `Fixture.winner` and result comparisons.
OUTCOME_HOME = "HOME"
OUTCOME_AWAY = "AWAY"
OUTCOME_DRAW = "DRAW"

# Round lifecycle - admin-curated, see app/round_helpers.py and the admin blueprint's
# create/publish/complete actions for the rules around each transition.
#   DRAFT    - admin is preparing it (naming it, assigning fixtures); invisible to users.
#   ACTIVE   - the one round currently open to/visible by users; predictions + live results.
#   COMPLETE - locked, settled, and archived for reference (leaderboards/history).
ROUND_STATUS_DRAFT = "DRAFT"
ROUND_STATUS_ACTIVE = "ACTIVE"
ROUND_STATUS_COMPLETE = "COMPLETE"
ROUND_STATUSES = (ROUND_STATUS_DRAFT, ROUND_STATUS_ACTIVE, ROUND_STATUS_COMPLETE)

PREDICTION_LOCK_MINUTES_BEFORE_KICKOFF = 5

# Used as the round's stake when the admin doesn't set one explicitly when creating it.
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
    # their predictions and round entries (opt-ins included - opted_in lives on
    # RoundEntry) with them, or the delete fails on the FK / leaves orphans that
    # keep them showing up in the players grid and standings.
    predictions = db.relationship("Prediction", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        # Explicit method: some platforms' Python builds lack hashlib.scrypt (werkzeug's default).
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class Round(db.Model):
    __tablename__ = "rounds"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # Determines display/processing order, e.g. 1 = Group Stage Week 1 ... 8 = Final.
    # Auto-assigned as (previous round's sequence + 1) when a draft is created - rounds
    # are always prepared and played in order, even if drafted ahead of time.
    sequence = db.Column(db.Integer, unique=True, nullable=False)
    # Per-round buy-in - sized for GBP to the penny. Set by the admin when creating the
    # round (see admin.create_round); defaults to DEFAULT_STAKE_AMOUNT when left blank.
    stake_amount = db.Column(db.Numeric(8, 2), nullable=False, default=DEFAULT_STAKE_AMOUNT)
    status = db.Column(db.String(16), nullable=False, default=ROUND_STATUS_DRAFT, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Dev/testing escape hatch: lets an admin lock a round on demand, bypassing the
    # kick-off-based lock_time below. See admin.force_lock_round.
    force_locked = db.Column(db.Boolean, nullable=False, default=False)

    fixtures = db.relationship(
        "Fixture",
        back_populates="round",
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
        """Predictions for the round lock 5 minutes before its earliest kick-off."""
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
        is an explicit admin decision to archive the round - see admin.complete_round).
        It's used to inform the admin when a round looks ready to be marked complete.
        """
        fixtures = self.fixtures.all()
        return bool(fixtures) and self.is_locked and all(f.is_finished for f in fixtures)

    def __repr__(self):
        return f"<Round {self.sequence}: {self.name} [{self.status}]>"


class Fixture(db.Model):
    __tablename__ = "fixtures"

    id = db.Column(db.Integer, primary_key=True)
    # football-data.org match id - lets us reconcile synced fixtures/results.
    external_id = db.Column(db.Integer, unique=True, nullable=True, index=True)

    round_id = db.Column(db.Integer, db.ForeignKey("rounds.id"), nullable=True, index=True)
    round = db.relationship("Round", back_populates="fixtures")

    home_team = db.Column(db.String(120), nullable=False)
    away_team = db.Column(db.String(120), nullable=False)
    home_short_name = db.Column(db.String(80), nullable=True)
    away_short_name = db.Column(db.String(80), nullable=True)

    # Free-text descriptors from the API, e.g. stage="GROUP_STAGE", group="Group A".
    stage = db.Column(db.String(64), nullable=True)
    group_name = db.Column(db.String(64), nullable=True)

    kickoff_at = db.Column(db.DateTime, nullable=False, index=True)

    # Knockout matches score against the 90-minute result only; group matches
    # never go to extra time so this flag simplifies the scoring branch.
    is_knockout = db.Column(db.Boolean, nullable=False, default=False)

    # football-data.org status, e.g. SCHEDULED / TIMED / IN_PLAY / PAUSED / FINISHED / POSTPONED.
    status = db.Column(db.String(32), nullable=False, default="SCHEDULED")

    # Score after 90 minutes of regulation - this is what predictions are scored against,
    # even for knockout matches that go on to extra time / penalties.
    home_score_90 = db.Column(db.Integer, nullable=True)
    away_score_90 = db.Column(db.Integer, nullable=True)

    # Final outcome of the match (after ET/penalties if applicable). Always HOME or AWAY
    # for knockout fixtures - "correct result" there means picking the side that advances,
    # regardless of the 90-minute scoreline. DRAW is only possible in group-stage matches.
    winner = db.Column(db.String(8), nullable=True)

    last_synced_at = db.Column(db.DateTime, nullable=True)

    predictions = db.relationship("Prediction", back_populates="fixture", lazy="dynamic")

    # football-data.org statuses while a match is being played - used to drive the
    # live-score display on the results page (see main.round_results / round_live_scores).
    # SUSPENDED (e.g. weather delay) is treated as still "live" - the match isn't over.
    _LIVE_STATUSES = ("IN_PLAY", "PAUSED", "SUSPENDED")

    # AWARDED covers matches decided without (full) play, e.g. a forfeit - these carry a
    # final score just like FINISHED. football-data.org's `score.fullTime` can be populated
    # with the *current* score while a match is still IN_PLAY, so `is_finished` must key off
    # `status` rather than just score presence.
    _FINISHED_STATUSES = ("FINISHED", "AWARDED")

    @property
    def is_finished(self):
        return (
            self.status in self._FINISHED_STATUSES
            and self.home_score_90 is not None
            and self.away_score_90 is not None
        )

    @property
    def is_live(self):
        return self.status in self._LIVE_STATUSES

    @property
    def elapsed_minutes(self):
        """Rough match minute, derived from kickoff time.

        The free football-data.org tier doesn't reliably expose a live "minute" field,
        so we approximate from elapsed wall-clock time since kickoff. Capped at 90 -
        stoppage time/half-time breaks make a precise figure impossible without richer
        live data, and an approximation beyond 90' would be misleading either way.
        """
        if not self.is_live:
            return None
        elapsed = int((datetime.utcnow() - self.kickoff_at).total_seconds() // 60)
        return max(0, min(elapsed, 90))

    @property
    def result_outcome(self):
        """HOME/AWAY/DRAW based on the 90-minute score - used for group-stage scoring."""
        if not self.is_finished:
            return None
        if self.home_score_90 > self.away_score_90:
            return OUTCOME_HOME
        if self.away_score_90 > self.home_score_90:
            return OUTCOME_AWAY
        return OUTCOME_DRAW

    def __repr__(self):
        return f"<Fixture {self.home_team} v {self.away_team} @ {self.kickoff_at}>"


class RoundEntry(db.Model):
    """A user's opt-in/out decision for a round's £5 pot (see app/finance.py).

    Opting in is financial only - it doesn't gate prediction *visibility*, but the app
    only lets opted-in users submit predictions and only shows opted-in users in the
    players grid. A row is created on the user's first toggle and then flipped in place,
    so `opted_in` always reflects their latest choice (allowed any time up to lock).
    """

    __tablename__ = "round_entries"
    __table_args__ = (
        db.UniqueConstraint("user_id", "round_id", name="uq_round_entry_user_round"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    round_id = db.Column(db.Integer, db.ForeignKey("rounds.id"), nullable=False, index=True)
    opted_in = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("round_entries", lazy="dynamic", cascade="all, delete-orphan"))
    round = db.relationship("Round", backref=db.backref("entries", lazy="dynamic"))

    def __repr__(self):
        return f"<RoundEntry user={self.user_id} round={self.round_id} opted_in={self.opted_in}>"


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
    # "live" (3-minute polling during a match window), "daily" (06:00 UTC sync), or "manual" (admin-triggered).
    mode = db.Column(db.String(16), nullable=False)
    succeeded = db.Column(db.Boolean, nullable=False, default=True)
    fixtures_created = db.Column(db.Integer, nullable=False, default=0)
    fixtures_updated = db.Column(db.Integer, nullable=False, default=0)
    fixtures_scored = db.Column(db.Integer, nullable=False, default=0)
    # Error message on failure, or notes such as fixtures flagged for manual ET/penalty review.
    detail = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<PollLog {self.mode} @ {self.run_at} ok={self.succeeded} created={self.fixtures_created} updated={self.fixtures_updated}>"
