from flask import Blueprint, redirect, url_for
from flask_login import login_required

bp = Blueprint("predictions", __name__, url_prefix="/predictions")


@bp.route("/", methods=["GET", "POST"])
@login_required
def my_predictions():
    """Predictions are now entered/edited inline on the players home screen - keep
    this URL alive (redirecting there) for old bookmarks/links rather than 404ing."""
    return redirect(url_for("main.players"))
