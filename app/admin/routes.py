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
from ..models import Comment, Group, Post, User
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


@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "users": db.session.query(func.count(User.id)).scalar(),
        "posts": db.session.query(func.count(Post.id)).scalar(),
        "comments": db.session.query(func.count(Comment.id)).scalar(),
        "groups": db.session.query(func.count(Group.id)).scalar(),
        "banned": db.session.query(func.count(User.id)).filter_by(is_banned=True).scalar(),
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
    delete_upload("attachments", post.attachment)
    db.session.delete(post)
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
