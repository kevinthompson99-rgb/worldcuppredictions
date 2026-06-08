from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, ValidationError

from app.models import User


class RegistrationForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=64)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match")]
    )
    submit = SubmitField("Sign up")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("That username is already taken.")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError("That email is already registered.")


class CSRFForm(FlaskForm):
    """Empty form used purely to render a CSRF token for simple action buttons (sync, assign, etc)."""

    pass


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


def build_prediction_form(fixtures):
    """Dynamically build a form with a home/away score pair per fixture.

    Field names are namespaced by fixture id (e.g. `home_42`, `away_42`) so a single
    form can submit predictions for every match in a round at once.
    """

    class PredictionForm(FlaskForm):
        pass

    for fixture in fixtures:
        setattr(
            PredictionForm,
            f"home_{fixture.id}",
            IntegerField(validators=[DataRequired(), NumberRange(min=0, max=20)]),
        )
        setattr(
            PredictionForm,
            f"away_{fixture.id}",
            IntegerField(validators=[DataRequired(), NumberRange(min=0, max=20)]),
        )

    setattr(PredictionForm, "submit", SubmitField("Save predictions"))
    return PredictionForm()
