from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms import build_prediction_form
from app.models import Prediction
from app.round_helpers import get_active_round

bp = Blueprint("predictions", __name__, url_prefix="/predictions")


@bp.route("/", methods=["GET", "POST"])
@login_required
def my_predictions():
    round_ = get_active_round()
    if round_ is None:
        flash("There's no round currently open for predictions.", "info")
        return render_template("predictions/none_open.html")

    fixtures = round_.fixtures.all()
    if round_.is_locked:
        flash("Predictions for this round have locked.", "warning")
        return redirect(url_for("main.round_results", round_id=round_.id))

    existing = {
        p.fixture_id: p
        for p in current_user.predictions.filter(Prediction.fixture_id.in_([f.id for f in fixtures]))
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
            prediction = existing.get(fixture.id)
            if prediction is None:
                prediction = Prediction(user_id=current_user.id, fixture_id=fixture.id)
                db.session.add(prediction)
            prediction.predicted_home = home
            prediction.predicted_away = away
            prediction.updated_at = datetime.utcnow()

        db.session.commit()
        flash("Your predictions have been saved.", "success")
        return redirect(url_for("predictions.my_predictions"))

    if request.method == "GET":
        # Pre-fill the form with previously saved predictions.
        for fixture in fixtures:
            prediction = existing.get(fixture.id)
            if prediction is not None:
                getattr(form, f"home_{fixture.id}").data = prediction.predicted_home
                getattr(form, f"away_{fixture.id}").data = prediction.predicted_away

    return render_template(
        "predictions/edit.html",
        round=round_,
        fixtures=fixtures,
        form=form,
    )
