from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, InputRequired, Length, NumberRange, ValidationError
from wtforms.widgets import TextInput

from app.models import User


class RegistrationForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=64)])
    display_name = StringField(
        "Display name",
        validators=[DataRequired(), Length(min=1, max=64)],
        description="Shown to other players in the grid, leaderboard and pot — your username stays private.",
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match")]
    )
    submit = SubmitField("Sign up")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("That username is already taken.")


class AdminCreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=64)])
    display_name = StringField("Display name", validators=[DataRequired(), Length(min=1, max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    is_admin = BooleanField("Admin")
    submit = SubmitField("Add user")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("That username is already taken.")


class ProfileForm(FlaskForm):
    """Lets a player change the public-facing name shown on the grid/leaderboard/pot
    without touching their (private) login username."""

    display_name = StringField("Display name", validators=[DataRequired(), Length(min=1, max=64)])
    submit = SubmitField("Save")


class CSRFForm(FlaskForm):
    """Empty form used purely to render a CSRF token for simple action buttons (sync, assign, etc)."""

    pass


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class ScoreField(IntegerField):
    """An IntegerField rendered as a plain text input (not a number spinner/dropdown).

    `inputmode="numeric"` and `pattern="[0-9]*"` give mobile browsers a numeric keypad
    and let the browser flag non-digit input client-side, while the server-side
    validators below are the source of truth.
    """

    widget = TextInput()


SCORE_RANGE_MESSAGE = "Enter a score between 0 and 15."


def build_prediction_form(fixtures):
    """Dynamically build a form with a home/away score pair per fixture.

    Field names are namespaced by fixture id (e.g. `home_42`, `away_42`) so a single
    form can submit predictions for every match in a round at once.
    """

    class PredictionForm(FlaskForm):
        pass

    def make_score_field():
        return ScoreField(
            validators=[InputRequired(message=SCORE_RANGE_MESSAGE), NumberRange(min=0, max=15, message=SCORE_RANGE_MESSAGE)],
            render_kw={"inputmode": "numeric", "pattern": "[0-9]*", "maxlength": "2", "autocomplete": "off"},
        )

    for fixture in fixtures:
        setattr(PredictionForm, f"home_{fixture.id}", make_score_field())
        setattr(PredictionForm, f"away_{fixture.id}", make_score_field())

    setattr(PredictionForm, "submit", SubmitField("Save predictions"))
    return PredictionForm()
