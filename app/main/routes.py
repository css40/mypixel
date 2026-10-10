"""Rutas generales: archivos subidos, filtros Jinja y páginas de error."""
from pathlib import Path

from flask import abort, current_app, render_template, request, send_from_directory
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Notification, utcnow
from ..utils import render_markdown, strip_markdown
from . import bp


@bp.app_template_filter("md")
def md_filter(text):
    """Renderiza Markdown de un post a HTML seguro. Usar con: {{ post.body|md|safe }}"""
    return render_markdown(text)


@bp.app_template_filter("md_preview")
def md_preview_filter(text, length=200):
    """Vista previa en texto plano (sin sintaxis Markdown) para las tarjetas del feed."""
    plain = strip_markdown(text)
    return (plain[:length].rsplit(" ", 1)[0] + "…") if len(plain) > length else plain


@bp.route("/notifications")
@login_required
def notifications():
    page = request.args.get("page", 1, type=int)
    stmt = (db.select(Notification).options(joinedload(Notification.actor), joinedload(Notification.post))
            .where(Notification.user_id == current_user.id)
            .order_by(Notification.created_at.desc()))
    pagination = db.paginate(stmt, page=page, per_page=20, error_out=False)

    unread_ids = [n.id for n in pagination.items if n.read_at is None]
    if unread_ids:
        Notification.query.filter(Notification.id.in_(unread_ids)).update(
            {"read_at": utcnow()}, synchronize_session=False)
        db.session.commit()

    return render_template("notifications.html", pagination=pagination,
                           notifications=pagination.items)


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Sirve archivos subidos. Las imágenes se muestran inline; el resto se descarga
    (así evitamos que un .html subido se ejecute en nuestro dominio)."""
    root = Path(current_app.config["UPLOAD_FOLDER"])
    if not (root / filename).is_file():
        abort(404)
    ext = filename.rsplit(".", 1)[-1].lower()
    inline = ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]
    # Los nombres de archivo son UUID (nunca cambian de contenido): se pueden cachear.
    response = send_from_directory(root, filename, as_attachment=not inline,
                                   max_age=2592000 if inline else None)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.app_template_filter("timeago")
def timeago(dt) -> str:
    seconds = int((utcnow() - dt).total_seconds())
    if seconds < 60:
        return "hace un momento"
    for size, unit in ((86400 * 365, "año"), (86400 * 30, "mes"), (86400, "día"),
                       (3600, "hora"), (60, "min")):
        if seconds >= size:
            n = seconds // size
            if unit == "mes":
                return f"hace {n} {'mes' if n == 1 else 'meses'}"
            if unit == "año":
                return f"hace {n} {'año' if n == 1 else 'años'}"
            if unit == "día":
                return f"hace {n} {'día' if n == 1 else 'días'}"
            if unit == "hora":
                return f"hace {n} {'hora' if n == 1 else 'horas'}"
            return f"hace {n} min"
    return "hace un momento"


@bp.app_errorhandler(403)
def forbidden(_e):
    return render_template("errors/error.html", code=403,
                           message="No tienes permiso para hacer esto."), 403


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("errors/error.html", code=404,
                           message="No encontramos esa página."), 404


@bp.app_errorhandler(413)
def too_large(_e):
    return render_template("errors/error.html", code=413,
                           message="El archivo supera el límite de 8 MB."), 413


@bp.app_errorhandler(SQLAlchemyError)
def database_error(exc):
    """Cualquier fallo de BD que no se haya manejado antes: se revierte la sesión
    (para no dejar la conexión en un estado roto) y se muestra un mensaje claro."""
    db.session.rollback()
    current_app.logger.error("Error de base de datos", exc_info=(type(exc), exc, exc.__traceback__))
    return render_template("errors/error.html", code=503,
                           message="La base de datos no responde. Inténtalo en unos segundos."), 503


@bp.app_errorhandler(500)
def server_error(_e):
    db.session.rollback()
    return render_template("errors/error.html", code=500,
                           message="Algo salió mal por nuestra parte."), 500
