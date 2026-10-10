from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, StringField, SubmitField, TextAreaField
from wtforms.validators import Length, Optional, ValidationError


def _http_url(form, field):
    value = (field.data or "").strip()
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValidationError("Debe empezar por http:// o https://")


class EditProfileForm(FlaskForm):
    bio = TextAreaField("Biografía", validators=[Optional(), Length(max=500)])
    github_url = StringField("GitHub", validators=[Optional(), Length(max=255), _http_url])
    twitter_url = StringField("X / Twitter", validators=[Optional(), Length(max=255), _http_url])
    website_url = StringField("Sitio web", validators=[Optional(), Length(max=255), _http_url])
    avatar = FileField("Foto de perfil")
    remove_avatar = BooleanField("Quitar foto actual")
    submit = SubmitField("Guardar cambios")
