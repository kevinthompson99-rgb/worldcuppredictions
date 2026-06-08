from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.finance import STAKE_AMOUNT, is_opted_in
from app.forms import build_prediction_form
from app.models import Prediction
from app.round_helpers import get_active_round

bp = Blueprint("predictions", __name__, url_prefix="/predictions")


@bp.route("/", methods=["GET", "POST"])
@login_required
def my_predictions():
    """The dedicated screen for entering/editing your own predictions before the
    deadline. Requires having opted in to the round's pot first - the home screen
    prompts for that, and this view redirects back there with a nudge if someone
    lands here without doing so.
    """
    round_ = get_active_round()
    fixtures = round_.fixtures.all() if round_ is not None else []
    locked = round_ is not None and round_.is_locked
    opted_in = is_opted_in(current_user, round_) if round_ is not None else False

    if round_ is None or not fixtures or locked:
        return render_template("predictions/edit.html", round=round_, fixtures=fixtures, locked=locked, opted_in=opted_in, form=None)

    if not opted_in:
        flash(f"Join this round (£{STAKE_AMOUNT:.2f}) on the home screen before entering predictions.", "warning")
        return redirect(url_for("main.players"))

    predictions = {
        prediction.fixture_id: prediction
        for prediction in Prediction.query.filter(
            Prediction.user_id == current_user.id,
            Prediction.fixture_id.in_([fixture.id for fixture in fixtures]),
        )
    }

    form = build_prediction_form(fixtures)

    if form.validate_on_submit():
        # Re-check the lock at submission time - the deadline may have passed mid-edit.
        if round_.is_locked:
            flash("Sorry, predictions for this round just locked. Your changes were not saved.", "danger")
            return redirect(url_for("predictions.my_predictions"))

        for fixture in fixtures:
            home = getattr(form, f"home_{fixture.id}").data
            away = getattr(form, f"away_{fixture.id}").data
            prediction = predictions.get(fixture.id)
            if prediction is None:
                prediction = Prediction(user_id=current_user.id, fixture_id=fixture.id)
                db.session.add(prediction)
            prediction.predicted_home = home
            prediction.predicted_away = away
            prediction.updated_at = datetime.utcnow()

        db.session.commit()
        flash("Your predictions have been saved.", "success")
        return redirect(url_for("main.players"))

    for fixture in fixtures:
        prediction = predictions.get(fixture.id)
        if prediction is not None:
            getattr(form, f"home_{fixture.id}").data = prediction.predicted_home
            getattr(form, f"away_{fixture.id}").data = prediction.predicted_away

    return render_template(
        "predictions/edit.html",
        round=round_,
        fixtures=fixtures,
        locked=locked,
        opted_in=opted_in,
        form=form,
    )
