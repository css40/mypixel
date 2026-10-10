"""Utilidades: subida segura de archivos, Markdown seguro y helpers."""
import re as _re
import uuid
from pathlib import Path

import markdown as _markdown

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .sanitize import sanitize_html


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def save_attachment(file_storage):
    """Guarda un adjunto de post. Devuelve (nombre_guardado, nombre_original)
    o (None, None) si no hay archivo. Lanza ValueError si no está permitido."""
    # Con FlaskForm(obj=...) el campo puede contener un str si no se subió nada.
    if not isinstance(file_storage, FileStorage) or not file_storage.filename:
        return None, None
    original = secure_filename(file_storage.filename)
    ext = _ext(original)
    if ext not in current_app.config["ALLOWED_ATTACHMENT_EXTENSIONS"]:
        raise ValueError("Tipo de archivo no permitido.")
    stored = f"{uuid.uuid4().hex}.{ext}"
    dest = Path(current_app.config["UPLOAD_FOLDER"]) / "attachments" / stored
    file_storage.save(dest)
    return stored, original


def save_avatar(file_storage):
    """Valida que sea imagen, la recorta a 256x256 y la guarda como PNG."""
    if not isinstance(file_storage, FileStorage) or not file_storage.filename:
        return None
    if _ext(file_storage.filename) not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        raise ValueError("El avatar debe ser una imagen PNG, JPG, GIF o WEBP.")
    try:
        img = Image.open(file_storage.stream)
        img.draft("RGB", (512, 512))          # JPEG grandes: decodifica ya reducido (más rápido)
        img = ImageOps.exif_transpose(img).convert("RGBA")
        img = ImageOps.fit(img, (256, 256), Image.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("El archivo no es una imagen válida.")
    stored = f"{uuid.uuid4().hex}.png"
    img.save(Path(current_app.config["UPLOAD_FOLDER"]) / "avatars" / stored, "PNG")
    return stored


def delete_upload(subfolder: str, filename) -> None:
    if not filename:
        return
    path = Path(current_app.config["UPLOAD_FOLDER"]) / subfolder / filename
    try:
        path.unlink(missing_ok=True)
    except OSError:
        current_app.logger.warning("No se pudo borrar %s", path)


def escape_like(term: str) -> str:
    """Escapa comodines de LIKE para búsquedas literales."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_safe_next(target) -> bool:
    """Evita open-redirect en el parámetro ?next=."""
    return bool(target) and target.startswith("/") and not target.startswith("//")


# --------------------------------------------------------------------------- #
# Markdown, etiquetas
# --------------------------------------------------------------------------- #
def render_markdown(text: str) -> str:
    """Convierte Markdown de un post a HTML seguro (sanitizado) para usar con |safe."""
    if not text:
        return ""
    html = _markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    return sanitize_html(html)


_MD_STRIP_RE = _re.compile(r"(`{1,3}.*?`{1,3}|[#*_>~\[\]()!-]|https?://\S+)", _re.DOTALL)


def strip_markdown(text: str) -> str:
    """Texto plano aproximado para previsualizaciones cortas (tarjetas del feed)."""
    if not text:
        return ""
    return _MD_STRIP_RE.sub(" ", text).strip()


def parse_tags(raw: str, limit: int = 6) -> list[str]:
    """'Python, Flask , web ' -> ['Python', 'Flask', 'web'] sin duplicados, máx `limit`."""
    seen, result = set(), []
    for chunk in (raw or "").split(","):
        name = chunk.strip()[:40]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        result.append(name)
        if len(result) >= limit:
            break
    return result
