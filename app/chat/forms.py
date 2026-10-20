from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length, ValidationError

from ..models import Group


class GroupForm(FlaskForm):
    name = StringField("Nombre del grupo", validators=[DataRequired(), Length(3, 60)])
    description = StringField("Descripción", validators=[Length(max=255)])
    submit = SubmitField("Crear grupo")

    def validate_name(self, field):
        if Group.query.filter(func.lower(Group.name) == field.data.strip().lower()).first():
            raise ValidationError("Ya existe un grupo con ese nombre.")
