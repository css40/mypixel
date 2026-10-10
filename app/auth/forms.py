from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, Length, Regexp,
                                ValidationError)

from ..models import User


class RegisterForm(FlaskForm):
    username = StringField("Nombre de usuario", validators=[
        DataRequired(), Length(3, 30),
        Regexp(r"^[A-Za-z0-9_]+$", message="Solo letras, números y guion bajo."),
    ])
    email = StringField("Correo", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Contraseña", validators=[DataRequired(), Length(min=8, max=128)])
    confirm = PasswordField("Repite la contraseña", validators=[
        DataRequired(), EqualTo("password", message="Las contraseñas no coinciden."),
    ])
    submit = SubmitField("Crear cuenta")

    def validate_username(self, field):
        if User.query.filter(func.lower(User.username) == field.data.lower()).first():
            raise ValidationError("Ese nombre de usuario ya está en uso.")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError("Ya existe una cuenta con ese correo.")


class LoginForm(FlaskForm):
    email = StringField("Correo", validators=[DataRequired(), Email()])
    password = PasswordField("Contraseña", validators=[DataRequired()])
    remember = BooleanField("Mantener sesión iniciada")
    submit = SubmitField("Entrar")
