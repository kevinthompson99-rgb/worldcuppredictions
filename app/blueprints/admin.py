from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin_utils import admin_required
from app.extensions import db
from app.finance import all_gameweeks_financial_summary, set_gameweek_stake
from app.forms import AdminCreateUserForm, AdminEditUserForm, CSRFForm
from app.gameweek_helpers import get_active_gameweek, get_draft_gameweek, get_draft_gameweeks
from app.leaderboards import gameweek_leaderboard
from app.models import (
    GAMEWEEK_STATUS_ACTIVE,
    GAMEWEEK_STATUS_COMPLETE,
    GAMEWEEK_STATUS_DRAFT,
    Fixture,
    Gameweek,
    Prediction,
    PollLog,
    User,
)
from app.push import notify_all, notify_gameweek_participants
from app.scoring import score_fixture
from app.sync import sync_fixtures_and_results
from app.time_utils import to_london

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
@admin_required
def require_admin():
    pass


@bp.route("/")
def dashboard():
    draft_gameweeks = get_draft_gameweeks()
    return render_template(
        "admin/dashboard.html",
        active_gameweek=get_active_gameweek(),
        draft_count=len(draft_gameweeks),
        next_draft_gameweek=draft_gameweeks[0] if draft_gameweeks else None,
        gameweek_count=Gameweek.query.count(),
        fixture_count=Fixture.query.count(),
        unassigned_count=Fixture.query.filter(Fixture.gameweek_id.is_(None)).count(),
        user_count=User.query.count(),
        last_poll=PollLog.query.order_by(PollLog.run_at.desc()).first(),
        form=CSRFForm(),
    )


@bp.route("/finance")
def finance():
    return render_template(
        "admin/finance.html",
        summaries=all_gameweeks_financial_summary(),
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
                f"Rearranged fixture(s) - matchday changed after their gameweek was "
                f"published: fixture id(s) {summary['flagged_for_review']}"
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
            "The API reports a different gameweek for these fixtures than the one they're "
            f"currently assigned to - review and re-home manually if needed: "
            f"fixture id(s) {summary['flagged_for_review']}",
            "warning",
        )
    return redirect(url_for("admin.dashboard"))


@bp.route("/gameweeks")
def gameweeks():
    return render_template(
        "admin/gameweeks.html",
        gameweeks=Gameweek.query.order_by(Gameweek.matchday.asc()).all(),
        form=CSRFForm(),
    )


@bp.route("/gameweeks/<int:gameweek_id>")
def gameweek_detail(gameweek_id):
    gameweek = Gameweek.query.get_or_404(gameweek_id)
    unassigned = (
        Fixture.query.filter(
            Fixture.gameweek_id.is_(None),
            Fixture.kickoff_at > datetime.utcnow()
        ).order_by(Fixture.kickoff_at.asc()).all()
    )
    return render_template(
        "admin/gameweek_detail.html",
        gameweek=gameweek,
        fixtures=gameweek.fixtures.all(),
        unassigned=unassigned,
        active_gameweek=get_active_gameweek(),
        form=CSRFForm(),
    )


@bp.route("/gameweeks/<int:gameweek_id>/stake", methods=["POST"])
def set_stake(gameweek_id):
    """Override a gameweek's stake amount (defaults to DEFAULT_STAKE_AMOUNT on auto-create)."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    gameweek = Gameweek.query.get_or_404(gameweek_id)
    try:
        set_gameweek_stake(gameweek, request.form.get("stake_amount", "").strip())
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    flash(f"Stake for '{gameweek.name}' set to £{gameweek.stake_amount:.2f}.", "success")
    return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))


@bp.route("/gameweeks/<int:gameweek_id>/publish", methods=["POST"])
def publish_gameweek(gameweek_id):
    """Move a DRAFT gameweek to ACTIVE, making it visible to users for predictions."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    gameweek = Gameweek.query.get_or_404(gameweek_id)
    if gameweek.status != GAMEWEEK_STATUS_DRAFT:
        flash(f"'{gameweek.name}' isn't a draft - it can't be published.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    active = get_active_gameweek()
    if active is not None:
        flash(
            f"'{active.name}' is still active - mark it complete before publishing the next gameweek.",
            "danger",
        )
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    if gameweek.fixtures.count() == 0:
        flash("This gameweek has no fixtures assigned yet - sync or assign some before publishing.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    if gameweek.is_locked:
        flash(
            "This gameweek's lock time has already passed (its earliest kick-off is too soon/in "
            "the past) - check the assigned fixtures' kick-off times before publishing.",
            "danger",
        )
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    gameweek.status = GAMEWEEK_STATUS_ACTIVE
    db.session.commit()

    notify_all(
        "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F "
        f"{gameweek.name} is now open",
        f"Make your predictions before {to_london(gameweek.lock_time)}. Good luck!",
        url="/players",
    )

    flash(f"'{gameweek.name}' is now live - users can see it and submit predictions.", "success")
    return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))


def _notify_gameweek_winner(gameweek):
    """Push the pot result to opted-in entrants once a gameweek settles.

    Winner(s) are the opted-in entrant(s) with the highest gameweek points - the same
    definition app.finance.gameweek_financial_summary uses to decide who splits the pot.
    """
    entrant_ids = {entry.user_id for entry in gameweek.entries.filter_by(opted_in=True)}
    if not entrant_ids:
        return

    ranked = [(user, points) for user, points, _ in gameweek_leaderboard(gameweek) if user.id in entrant_ids]
    if not ranked:
        return

    top_score = ranked[0][1]
    winners = [user for user, points in ranked if points == top_score]
    names = [user.display_name for user in winners]

    if len(names) == 1:
        body = f"{names[0]} wins {gameweek.name} with {top_score} pts!"
    elif len(names) == 2:
        body = f"{names[0]} & {names[1]} tie with {top_score} pts!"
    else:
        body = f"{', '.join(names[:-1])} & {names[-1]} tie with {top_score} pts!"

    notify_gameweek_participants(gameweek, f"\U0001F3C6 {gameweek.name} result", body, url="/leaderboard")


@bp.route("/gameweeks/<int:gameweek_id>/complete", methods=["POST"])
def complete_gameweek(gameweek_id):
    """Move an ACTIVE gameweek to COMPLETE, archiving it for reference."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    gameweek = Gameweek.query.get_or_404(gameweek_id)
    if gameweek.status != GAMEWEEK_STATUS_ACTIVE:
        flash(f"'{gameweek.name}' isn't active - it can't be marked complete.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    if not gameweek.is_locked:
        flash("This gameweek hasn't locked yet - predictions are still open, so it can't be completed.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    gameweek.status = GAMEWEEK_STATUS_COMPLETE
    db.session.commit()

    if gameweek.all_fixtures_settled:
        _notify_gameweek_winner(gameweek)
        flash(f"'{gameweek.name}' is complete and archived.", "success")
    else:
        flash(
            f"'{gameweek.name}' is archived, but not every fixture has a final score yet - "
            "double-check results and re-sync if needed (predictions can still be rescored later).",
            "warning",
        )
    return redirect(url_for("admin.gameweeks"))


@bp.route("/gameweeks/<int:gameweek_id>/force-lock", methods=["POST"])
def force_lock_gameweek(gameweek_id):
    """Dev/testing only: immediately lock a gameweek, bypassing its kick-off-based lock_time."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    gameweek = Gameweek.query.get_or_404(gameweek_id)
    if gameweek.status != GAMEWEEK_STATUS_ACTIVE:
        flash(f"'{gameweek.name}' isn't active - it can't be force-locked.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    if gameweek.is_locked:
        flash(f"'{gameweek.name}' is already locked.", "info")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    gameweek.force_locked = True
    db.session.commit()
    flash(f"'{gameweek.name}' is now force-locked - predictions are closed.", "success")
    return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))


@bp.route("/gameweeks/<int:gameweek_id>/assign", methods=["POST"])
def assign_fixtures(gameweek_id):
    """Bulk-assign the fixtures the admin checked on the gameweek management page.

    Mainly an escape hatch for rearranged fixtures the sync guard flagged rather than
    auto-moved (see app/sync.py) - the common case is sync assigning fixtures itself.
    """
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    gameweek = Gameweek.query.get_or_404(gameweek_id)
    if gameweek.is_locked:
        flash("This gameweek has already locked - fixtures can no longer be assigned to it.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    fixture_ids = request.form.getlist("fixture_ids", type=int)
    if not fixture_ids:
        flash("Select at least one fixture to assign.", "warning")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    fixtures = Fixture.query.filter(Fixture.id.in_(fixture_ids), Fixture.gameweek_id.is_(None)).all()
    for fixture in fixtures:
        fixture.gameweek_id = gameweek.id
    db.session.commit()

    flash(f"Assigned {len(fixtures)} fixture(s) to {gameweek.name}.", "success")
    return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))


@bp.route("/fixtures/<int:fixture_id>/unassign", methods=["POST"])
def unassign_fixture(fixture_id):
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    fixture = Fixture.query.get_or_404(fixture_id)
    gameweek_id = fixture.gameweek_id
    gameweek = fixture.gameweek

    if gameweek is not None and gameweek.is_locked:
        flash("This gameweek has already locked - fixtures can no longer be removed from it.", "danger")
        return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))

    fixture.gameweek_id = None
    db.session.commit()
    flash(f"Removed {fixture.home_team} v {fixture.away_team} from {gameweek.name if gameweek else 'its gameweek'}.", "info")
    return redirect(url_for("admin.gameweek_detail", gameweek_id=gameweek_id))


@bp.route("/fixtures")
def fixtures():
    """All fixtures, for score correction. Defaults to hiding fixtures whose gameweek is
    already COMPLETE - with the full season synced upfront, this list would otherwise
    only ever grow (380 fixtures/season) and bury the current/recent ones that actually
    need attention. ?all=1 shows the complete unfiltered list for the rare case an old
    result needs correcting.
    """
    show_all = request.args.get("all") == "1"
    query = Fixture.query
    if not show_all:
        query = query.filter(
            db.or_(Fixture.gameweek_id.is_(None), Fixture.gameweek.has(Gameweek.status != GAMEWEEK_STATUS_COMPLETE))
        )
    return render_template(
        "admin/fixtures.html",
        fixtures=query.order_by(Fixture.kickoff_at.asc()).all(),
        show_all=show_all,
        form=CSRFForm(),
    )


@bp.route("/fixtures/<int:fixture_id>/edit", methods=["POST"])
def edit_fixture(fixture_id):
    """Manually correct a fixture's score - a general data-quality tool (e.g. a sync ran
    mid-VAR-review, or before a postponed match's re-fixture date synced correctly)."""
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    fixture = Fixture.query.get_or_404(fixture_id)

    home_raw = request.form.get("home_score", "").strip()
    away_raw = request.form.get("away_score", "").strip()

    if home_raw.isdigit() and away_raw.isdigit():
        fixture.home_score = int(home_raw)
        fixture.away_score = int(away_raw)
        fixture.manually_corrected = True

    db.session.commit()

    updated = score_fixture(fixture)
    db.session.commit()

    flash(f"Fixture updated and {updated} prediction(s) (re)scored.", "success")
    return redirect(url_for("admin.fixtures"))


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
            display_name=form.display_name.data.strip(),
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


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = AdminEditUserForm(user_id=user.id, obj=user)
    if form.validate_on_submit():
        user.username = form.username.data.strip()
        user.display_name = form.display_name.data.strip()
        if form.password.data:
            user.set_password(form.password.data)
        user.is_admin = form.is_admin.data
        db.session.commit()
        flash(f"Saved changes to '{user.display_name}'.", "success")
        return redirect(url_for("admin.edit_user", user_id=user.id))
    return render_template("admin/user_detail.html", user=user, form=form, delete_form=CSRFForm())


@bp.route("/users/<int:user_id>/predictions", methods=["GET", "POST"])
def edit_user_predictions(user_id):
    user = User.query.get_or_404(user_id)
    gameweek = get_active_gameweek()
    if gameweek is None:
        flash("No active gameweek to edit predictions for.", "warning")
        return redirect(url_for("admin.edit_user", user_id=user_id))

    fixtures = gameweek.fixtures.order_by(Fixture.kickoff_at.asc()).all()

    if request.method == "POST":
        form = CSRFForm()
        if not form.validate_on_submit():
            abort(400, description="Invalid or missing CSRF token.")
        for fixture in fixtures:
            home_raw = request.form.get(f"home_{fixture.id}", "").strip()
            away_raw = request.form.get(f"away_{fixture.id}", "").strip()
            if home_raw == "" or away_raw == "":
                continue
            try:
                home = int(home_raw)
                away = int(away_raw)
            except ValueError:
                flash(f"Invalid score for {fixture.home_team} v {fixture.away_team}.", "danger")
                continue
            prediction = Prediction.query.filter_by(user_id=user.id, fixture_id=fixture.id).first()
            if prediction is None:
                prediction = Prediction(user_id=user.id, fixture_id=fixture.id, predicted_home=home, predicted_away=away)
                db.session.add(prediction)
            else:
                prediction.predicted_home = home
                prediction.predicted_away = away
        db.session.commit()
        flash(f"Predictions updated for {user.display_name}.", "success")
        return redirect(url_for("admin.edit_user_predictions", user_id=user_id))

    predictions = {p.fixture_id: p for p in Prediction.query.filter_by(user_id=user.id).join(Fixture).filter(Fixture.gameweek_id == gameweek.id).all()}
    return render_template(
        "admin/user_predictions.html",
        user=user,
        gameweek=gameweek,
        fixtures=fixtures,
        predictions=predictions,
        form=CSRFForm(),
    )


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400, description="Invalid or missing CSRF token.")

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't delete your own account.", "danger")
        return redirect(url_for("admin.users"))

    username = user.username
    # Cascade (User.predictions / GameweekEntry.user, see models.py) takes their
    # predictions, gameweek entries and opt-ins with them, so they also vanish
    # immediately from the players grid, standings and pot calculations.
    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted user '{username}' and all their predictions/gameweek entries.", "info")
    return redirect(url_for("admin.users"))


# TODO: temporary - remove once push notifications have been verified working end to end.
@bp.route("/push/test", methods=["POST"])
def test_push():
    form = CSRFForm()
    if not form.validate_on_submit():
        abort(400)
    from app.push import notify_all
    sent, total = notify_all(
        title="\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F LEPREM Test",
        body="Push notifications are working!",
        url="/"
    )
    if total == 0:
        flash(
            "No push subscriptions found - nothing to send. Enable notifications for this "
            "device from the Notifications screen first.",
            "warning",
        )
    elif sent == total:
        flash(f"Test notification sent to all {total} subscriber(s).", "success")
    else:
        flash(f"Sent to {sent} of {total} subscriber(s) - check server logs for the rest.", "warning")
    return redirect(url_for("admin.dashboard"))
