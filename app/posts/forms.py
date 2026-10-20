from flask import current_app
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (FieldList, FormField, SelectField, StringField, SubmitField,
                     TextAreaField)
from wtforms.validators import DataRequired, Length, Optional


class CodeBlockForm(FlaskForm):
    """Un bloque de código dentro del post. `class Meta` desactiva el CSRF propio,
    porque va anidado dentro de PostForm (que ya lleva su token)."""
    class Meta:
        csrf = False

    label = StringField("Título del bloque (opcional)", validators=[Optional(), Length(max=60)])
    language = SelectField("Lenguaje")
    code = TextAreaField("Código", validators=[Optional(), Length(max=20000)])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.language.choices = [(name, name) for name, _ in current_app.config["LANGUAGES"]]


class PostForm(FlaskForm):
    title = StringField("Título", validators=[DataRequired(), Length(3, 150)])
    language = SelectField("Categoría principal", validators=[DataRequired()])
    body = TextAreaField("Explicación (admite Markdown)", validators=[DataRequired(), Length(max=10000)])
    tags = StringField("Etiquetas", validators=[Optional(), Length(max=200)])
    code_blocks = FieldList(FormField(CodeBlockForm), min_entries=1, max_entries=5)
    attachment = FileField("Adjunto (opcional)")
    submit = SubmitField("Publicar")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.language.choices = [(name, name) for name, _ in current_app.config["LANGUAGES"]]

    def filled_code_blocks(self):
        """Solo los bloques donde el usuario realmente escribió código, en orden."""
        return [f for f in self.code_blocks if (f.code.data or "").strip()]


class CommentForm(FlaskForm):
    body = TextAreaField("Comentario", validators=[DataRequired(), Length(max=2000)])
    submit = SubmitField("Comentar")
