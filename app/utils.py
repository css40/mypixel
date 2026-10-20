"""Utilidades: subida segura de archivos, Markdown seguro y helpers."""
import re as _re
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import markdown as _markdown
import requests

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .sanitize import sanitize_html


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _upload_image_to_imgbb(file, filename: str) -> str:
    """Sube una imagen a ImgBB y devuelve su URL HTTPS permanente."""
    api_key = current_app.config.get("IMGBB_API_KEY")
    if not api_key:
        raise ValueError(
            "El almacenamiento de imágenes no está configurado. "
            "Contacta al administrador."
        )
    try:
        response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key},
            files={"image": (filename, file)},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.error("Falló la subida de imagen a ImgBB: %s", exc)
        raise ValueError(
            "No se pudo subir la imagen al almacenamiento. Inténtalo de nuevo."
        ) from exc

    image_data = result.get("data") if isinstance(result, dict) else None
    image_url = image_data.get("url") if isinstance(image_data, dict) else None
    if not isinstance(image_url, str) or urlsplit(image_url).scheme != "https":
        current_app.logger.error("ImgBB no devolvió una URL HTTPS válida.")
        raise ValueError("No se pudo obtener la URL segura de la imagen.")
    return image_url


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
    if ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        file_storage.stream.seek(0)
        image_url = _upload_image_to_imgbb(file_storage.stream, original)
        return image_url, original

    stored = f"{uuid.uuid4().hex}.{ext}"
    dest = Path(current_app.config["UPLOAD_FOLDER"]) / "attachments" / stored
    file_storage.save(dest)
    return stored, original


def save_avatar(file_storage):
    """Valida que sea imagen, la recorta a 256x256 y la sube a ImgBB."""
    if not isinstance(file_storage, FileStorage) or not file_storage.filename:
        return None
    if _ext(file_storage.filename) not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        raise ValueError("El avatar debe ser una imagen PNG, JPG, GIF o WEBP.")
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img).convert("RGBA")
        img = ImageOps.fit(img, (256, 256), Image.LANCZOS)
    except (UnidentifiedImageError, OSError):
        raise ValueError("El archivo no es una imagen válida.")
    image = BytesIO()
    image.name = "avatar.png"
    img.save(image, "PNG")
    image.seek(0)
    return _upload_image_to_imgbb(image, image.name)


def delete_upload(subfolder: str, filename) -> None:
    if not filename or urlsplit(str(filename)).scheme in {"http", "https"}:
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
