from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin_utils import admin_required
from app.extensions import db
from app.finance import all_rounds_financial_summary
from app.forms import AdminCreateUserForm, CSRFForm
from app.models import (
    ROUND_STATUS_ACTIVE,
    ROUND_STATUS_COMPLETE,
    ROUND_STATUS_DRAFT,
    Fixture,
    PollLog,
    Prediction,
    Round,
    RoundEntry,
    User,
)
from app.round_helpers import get_active_round, get_draft_round
from app.scoring import score_fixture
from app.sync import sync_fixtures_and_results

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
@admin_required
def require_admin():
    pass


@bp.route("/")
def dashboard():
    return render_template(
        "admin/dashboard.html",
        active_round=get_active_round(),
        draft_round=get_draft_round(),
        round_count=Round.query.count(),
        fixture_count=Fixture.query.count(),
        unassigned_count=Fixture.query.filter(Fixture.round_id.is_(None)).count(),
        user_count=User.query.count(),
        last_poll=PollLog.query.order_by(PollLog.run_at.desc()).first(),
        form=CSRFForm(),
    )


@bp.route("/finance")
def finance():
    return render_template(
        "admin/finance.html",
        summaries=all_rounds_financial_summary(),
    )


@bp.route("/polling")
def polling():
    return render_template(
        "admin/polling.html",
        logs=PollLog.query.order_by(PollLog.run_at.desc()).limit(100).all(),
    )


@bp.route("/sync", methods=["POST"])
def trigger_sync():
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    try:
        summary = sync_fixtures_and_results()
    except Exception as exc:  # network/API errors shouldn't crash the admin panel
        db.session.add(PollLog(mode="manual", succeeded=False, detail=str(exc)))
        db.session.commit()
        flash(f"Sync failed: {exc}", "danger")
        return redirect(url_for("admin.dashboard"))

    db.session.add(
        PollLog(
            mode="manual",
            fixtures_created=summary["created"],
            fixtures_updated=summary["updated"],
            fixtures_scored=summary["scored_fixtures"],
            detail=(
                f"Flagged for review (ET/penalties): fixture id(s) {summary['flagged_for_review']}"
                if summary["flagged_for_review"]
                else None
            ),
        )
    )
    db.session.commit()

    message = (
        f"Sync complete - {summary['created']} new fixture(s), "
        f"{summary['updated']} updated, {summary['scored_fixtures']} fixture(s) (re)scored."
    )
    flash(message, "success")
    if summary["flagged_for_review"]:
        flash(
            "These fixtures went to extra time/penalties - please verify their 90-minute "
            f"score is correct: fixture id(s) {summary['flagged_for_review']}",
            "warning",
        )
    return redirect(url_for("admin.dashboard"))


@bp.route("/rounds")
def rounds():
    return render_template(
        "admin/rounds.html",
        rounds=Round.query.order_by(Round.sequence.asc()).all(),
        # Drafts are prepared one at a time, regardless of whether a round is active -
        # that's the whole point of drafts (prep the next round while the current plays out).
        can_create_draft=(get_draft_round() is None),
        form=CSRFForm(),
    )


@bp.route("/rounds/new", methods=["POST"])
def create_round():
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    if get_draft_round() is not None:
        flash("There's already a draft round being prepared - finish that one first.", "danger")
        return redirect(url_for("admin.rounds"))

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name:
        flash("A round name is required.", "danger")
        return redirect(url_for("admin.rounds"))

    latest = Round.query.order_by(Round.sequence.desc()).first()
    next_sequence = (latest.sequence + 1) if latest else 1
    round_ = Round(name=name, description=description or None, sequence=next_sequence, status=ROUND_STATUS_DRAFT)
    db.session.add(round_)
    db.session.commit()
    flash(f"Draft round '{name}' created - assign its fixtures, then publish it when you're ready.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_.id))


@bp.route("/rounds/<int:round_id>")
def round_detail(round_id):
    round_ = Round.query.get_or_404(round_id)
    unassigned = (
        Fixture.query.filter(Fixture.round_id.is_(None)).order_by(Fixture.kickoff_at.asc()).all()
    )
    return render_template(
        "admin/round_detail.html",
        round=round_,
        fixtures=round_.fixtures.all(),
        unassigned=unassigned,
        active_round=get_active_round(),
        form=CSRFForm(),
    )


@bp.route("/rounds/<int:round_id>/publish", methods=["POST"])
def publish_round(round_id):
    """Move a DRAFT round to ACTIVE, making it visible to users for predictions."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    round_ = Round.query.get_or_404(round_id)
    if round_.status != ROUND_STATUS_DRAFT:
        flash(f"'{round_.name}' isn't a draft - it can't be published.", "danger")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    active = get_active_round()
    if active is not None:
        flash(
            f"'{active.name}' is still active - mark it complete before publishing the next round.",
            "danger",
        )
        return redirect(url_for("admin.round_detail", round_id=round_id))

    if round_.fixtures.count() == 0:
        flash("Assign at least one fixture before publishing this round.", "danger")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    if round_.is_locked:
        flash(
            "This round's lock time has already passed (its earliest kick-off is too soon/in the "
            "past) - check the assigned fixtures' kick-off times before publishing.",
            "danger",
        )
        return redirect(url_for("admin.round_detail", round_id=round_id))

    round_.status = ROUND_STATUS_ACTIVE
    db.session.commit()
    flash(f"'{round_.name}' is now live - users can see it and submit predictions.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_id))


@bp.route("/rounds/<int:round_id>/complete", methods=["POST"])
def complete_round(round_id):
    """Move an ACTIVE round to COMPLETE, archiving it for reference."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    round_ = Round.query.get_or_404(round_id)
    if round_.status != ROUND_STATUS_ACTIVE:
        flash(f"'{round_.name}' isn't active - it can't be marked complete.", "danger")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    if not round_.is_locked:
        flash("This round hasn't locked yet - predictions are still open, so it can't be completed.", "danger")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    round_.status = ROUND_STATUS_COMPLETE
    db.session.commit()

    if round_.all_fixtures_settled:
        flash(f"'{round_.name}' is complete and archived.", "success")
    else:
        flash(
            f"'{round_.name}' is archived, but not every fixture has a final score yet - "
            "double-check results and re-sync if needed (predictions can still be rescored later).",
            "warning",
        )
    return redirect(url_for("admin.rounds"))


@bp.route("/rounds/<int:round_id>/assign", methods=["POST"])
def assign_fixtures(round_id):
    """Bulk-assign the fixtures the admin checked on the round management page."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    round_ = Round.query.get_or_404(round_id)
    if round_.is_locked:
        flash("This round has already locked - fixtures can no longer be assigned to it.", "danger")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    fixture_ids = request.form.getlist("fixture_ids", type=int)
    if not fixture_ids:
        flash("Select at least one fixture to assign.", "warning")
        return redirect(url_for("admin.round_detail", round_id=round_id))

    fixtures = Fixture.query.filter(Fixture.id.in_(fixture_ids), Fixture.round_id.is_(None)).all()
    for fixture in fixtures:
        fixture.round_id = round_.id
    db.session.commit()

    flash(f"Assigned {len(fixtures)} fixture(s) to {round_.name}.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_id))


@bp.route("/fixtures/<int:fixture_id>/unassign", methods=["POST"])
def unassign_fixture(fixture_id):
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    fixture = Fixture.query.get_or_404(fixture_id)
    round_id = fixture.round_id
    round_ = fixture.round

    if round_ is not None and round_.is_locked:
        flash("This round has already locked - fixtures can no longer be removed from it.", "danger")
        return redirect(request.referrer or url_for("admin.round_detail", round_id=round_id))

    fixture.round_id = None
    db.session.commit()
    flash(f"Removed {fixture.home_team} v {fixture.away_team} from {round_.name if round_ else 'its round'}.", "info")
    return redirect(request.referrer or url_for("admin.round_detail", round_id=round_id))


@bp.route("/fixtures")
def fixtures():
    return render_template(
        "admin/fixtures.html",
        fixtures=Fixture.query.order_by(Fixture.kickoff_at.asc()).all(),
        form=CSRFForm(),
    )


@bp.route("/fixtures/<int:fixture_id>/edit", methods=["POST"])
def edit_fixture(fixture_id):
    """Manually correct the 90-minute score / winner / knockout flag.

    Needed because the football-data.org API doesn't cleanly separate the 90-minute
    score from the extra-time score for knockout matches (see app/sync.py).
    """
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    fixture = Fixture.query.get_or_404(fixture_id)

    home_raw = request.form.get("home_score_90", "").strip()
    away_raw = request.form.get("away_score_90", "").strip()
    winner = request.form.get("winner") or None
    fixture.is_knockout = bool(request.form.get("is_knockout"))

    if home_raw.isdigit() and away_raw.isdigit():
        fixture.home_score_90 = int(home_raw)
        fixture.away_score_90 = int(away_raw)
    if winner in ("HOME", "AWAY", "DRAW"):
        fixture.winner = winner

    db.session.commit()

    updated = score_fixture(fixture)
    db.session.commit()

    flash(f"Fixture updated and {updated} prediction(s) (re)scored.", "success")
    return redirect(request.referrer or url_for("admin.fixtures"))


@bp.route("/users")
def users():
    return render_template(
        "admin/users.html",
        users=User.query.order_by(User.username.asc()).all(),
        create_form=AdminCreateUserForm(),
        delete_form=CSRFForm(),
    )


@bp.route("/users/new", methods=["POST"])
def create_user():
    form = AdminCreateUserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.lower().strip(),
            is_admin=form.is_admin.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f"Added user '{user.username}'.", "success")
        return redirect(url_for("admin.users"))

    for field in form:
        for error in field.errors:
            flash(error, "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't delete your own account.", "danger")
        return redirect(url_for("admin.users"))

    has_activity = (
        Prediction.query.filter_by(user_id=user.id).first() is not None
        or RoundEntry.query.filter_by(user_id=user.id).first() is not None
    )
    if has_activity:
        flash(
            f"Can't delete '{user.username}' - they've already made predictions or joined a round's "
            "pot, and removing them would corrupt that history.",
            "danger",
        )
        return redirect(url_for("admin.users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted user '{user.username}'.", "info")
    return redirect(url_for("admin.users"))
