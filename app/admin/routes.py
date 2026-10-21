"""Panel de administración: estadísticas, moderación de usuarios y contenido.

Solo entra quien tenga `is_admin=True`. La primera cuenta admin la crea
automáticamente `_ensure_admin()` en app/__init__.py con las credenciales de
config.py / .env (ADMIN_EMAIL, ADMIN_PASSWORD).
"""
from functools import wraps

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import Comment, Group, Notification, Post, Report, User, utcnow
from ..utils import delete_upload
from . import bp


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.context_processor
def inject_pending_reports():
    """Número de reportes sin revisar, para el menú lateral del panel."""
    if current_user.is_authenticated and current_user.is_admin:
        return {"pending_reports": Report.query.filter_by(status="pending").count()}
    return {"pending_reports": 0}


def purge_post(post: Post) -> None:
    """Borra una publicación y deja limpias las referencias que la apuntan
    (avisos y reportes la guardan solo como referencia, no deben bloquear el borrado)."""
    Notification.query.filter_by(post_id=post.id).update({"post_id": None}, synchronize_session=False)
    Report.query.filter_by(post_id=post.id).update({"post_id": None}, synchronize_session=False)
    delete_upload("attachments", post.attachment)
    db.session.delete(post)


@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "users": db.session.query(func.count(User.id)).scalar(),
        "posts": db.session.query(func.count(Post.id)).scalar(),
        "comments": db.session.query(func.count(Comment.id)).scalar(),
        "groups": db.session.query(func.count(Group.id)).scalar(),
        "banned": db.session.query(func.count(User.id)).filter_by(is_banned=True).scalar(),
        "reports": Report.query.filter_by(status="pending").count(),
    }
    latest_posts = Post.query.order_by(Post.created_at.desc()).limit(5).all()
    latest_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    return render_template("admin/dashboard.html", stats=stats,
                           latest_posts=latest_posts, latest_users=latest_users)


@bp.route("/users")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    stmt = db.select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(func.lower(User.username).like(func.lower(like)) |
                          func.lower(User.email).like(func.lower(like)))
    pagination = db.paginate(stmt, page=page, per_page=25, error_out=False)
    return render_template("admin/users.html", pagination=pagination, users=pagination.items, q=q)


@bp.route("/users/<int:user_id>/toggle-ban", methods=["POST"])
@admin_required
def toggle_ban(user_id):
    user = db.get_or_404(User, user_id)
    if user.is_admin:
        flash("No puedes banear a otro administrador.", "error")
        return redirect(url_for("admin.users"))
    user.is_banned = not user.is_banned
    db.session.commit()
    flash(f"{'Suspendido' if user.is_banned else 'Reactivado'}: {user.username}.", "info")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_admin(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("No puedes quitarte el rol de administrador a ti mismo.", "error")
        return redirect(url_for("admin.users"))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash(f"{'Ahora es' if user.is_admin else 'Ya no es'} administrador: {user.username}.", "info")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/posts")
@admin_required
def posts():
    page = request.args.get("page", 1, type=int)
    stmt = db.select(Post).order_by(Post.created_at.desc())
    pagination = db.paginate(stmt, page=page, per_page=25, error_out=False)
    return render_template("admin/posts.html", pagination=pagination, posts=pagination.items)


@bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@admin_required
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)
    purge_post(post)
    db.session.commit()
    flash("Publicación eliminada por moderación.", "info")
    return redirect(request.referrer or url_for("admin.posts"))


@bp.route("/comments/<int:comment_id>/delete", methods=["POST"])
@admin_required
def delete_comment(comment_id):
    comment = db.get_or_404(Comment, comment_id)
    db.session.delete(comment)
    db.session.commit()
    flash("Comentario eliminado por moderación.", "info")
    return redirect(request.referrer or url_for("admin.dashboard"))


# --------------------------------------------------------------------------- #
# Reportes de publicaciones y perfiles
# --------------------------------------------------------------------------- #
@bp.route("/reports")
@admin_required
def reports():
    status = request.args.get("status", "pending")
    if status not in ("pending", "resolved"):
        status = "pending"
    page = request.args.get("page", 1, type=int)
    order = Report.created_at.asc() if status == "pending" else Report.resolved_at.desc()
    stmt = db.select(Report).where(Report.status == status).order_by(order)
    pagination = db.paginate(stmt, page=page, per_page=15, error_out=False)
    return render_template("admin/reports.html", pagination=pagination, reports=pagination.items,
                           status=status, decisions=Report.DECISIONS)


def _thanks_text(report: Report, note: str) -> str:
    """Texto detallado que recibe quien reportó: agradecimiento + decisión tomada."""
    target = (f"la publicación «{report.target_label}»" if report.kind == "post"
              else f"el perfil {report.target_label}")
    text = (f"¡Gracias por reportar! Revisamos tu reporte sobre {target} "
            f"(motivo: {report.reason_label}). Decisión: {report.decision_label}.")
    if note:
        text += f" Detalle del administrador: {note}"
    text += " Gracias por ayudarnos a mantener MyPixel como un lugar seguro."
    return text


@bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@admin_required
def resolve_report(report_id):
    report = db.get_or_404(Report, report_id)
    if report.status != "pending":
        flash("Ese reporte ya fue resuelto.", "info")
        return redirect(url_for("admin.reports"))

    decision = request.form.get("decision", "")
    note = request.form.get("note", "").strip()[:1000]
    allowed = ["dismiss", "warn", "ban"] + (["delete_post"] if report.kind == "post" else [])
    if decision not in allowed:
        flash("Elige una decisión válida.", "error")
        return redirect(url_for("admin.reports"))

    offender = report.reported_user
    if decision == "ban" and offender.is_admin:
        flash("No puedes suspender a un administrador.", "error")
        return redirect(url_for("admin.reports"))

    # Reportes pendientes sobre lo mismo: se resuelven juntos y todos reciben respuesta.
    same = Report.query.filter_by(status="pending", kind=report.kind)
    same = same.filter_by(post_id=report.post_id) if report.kind == "post" and report.post_id \
        else same.filter_by(reported_user_id=report.reported_user_id)
    batch = same.all()
    if report not in batch:
        batch.append(report)

    post = db.session.get(Post, report.post_id) if report.post_id else None
    if decision == "delete_post" and post:
        purge_post(post)
    elif decision == "ban":
        offender.is_banned = True
    elif decision == "warn":
        label = (f"tu publicación «{report.target_label}»" if report.kind == "post" else "tu perfil")
        Notification.notify(
            offender.id, current_user.id, "warning",
            message=(f"Recibiste una advertencia de la administración por {label} "
                     f"(motivo: {report.reason_label}). "
                     + (f"Detalle: {note} " if note else "")
                     + "Por favor, respeta las normas de la comunidad."))

    for r in batch:
        r.status, r.decision, r.admin_note = "resolved", decision, note or None
        r.resolved_at, r.resolved_by_id = utcnow(), current_user.id
        Notification.notify(r.reporter_id, current_user.id, "report_result",
                            post_id=r.post_id if decision != "delete_post" else None,
                            message=_thanks_text(r, note))
    db.session.commit()
    flash(f"Reporte resuelto ({report.decision_label}). Se avisó a quien reportó.", "success")
    return redirect(url_for("admin.reports"))
