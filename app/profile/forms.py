from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import RadioField, StringField, SubmitField, TextAreaField
from wtforms.validators import Length, Optional, ValidationError


def _http_url(form, field):
    value = (field.data or "").strip()
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValidationError("Debe empezar por http:// o https://")


class EditProfileForm(FlaskForm):
    # Avatar: "emoji" = avatar retro (emoji + color) · "image" = foto propia
    avatar_mode = RadioField("Tipo de avatar", choices=[("emoji", "Avatar retro"), ("image", "Mi imagen")],
                             default="emoji")
    avatar_emoji = StringField("Avatar", validators=[Optional(), Length(max=16)])
    avatar_color = StringField("Color", validators=[Optional(), Length(max=9)])
    avatar = FileField("Foto de perfil")

    bio = TextAreaField("Biografía", validators=[Optional(), Length(max=500)])
    github_url = StringField("GitHub", validators=[Optional(), Length(max=255), _http_url])
    twitter_url = StringField("X / Twitter", validators=[Optional(), Length(max=255), _http_url])
    website_url = StringField("Sitio web", validators=[Optional(), Length(max=255), _http_url])
    submit = SubmitField("Guardar cambios")
