from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import Follow, Notification, Post, User
from ..services import post_service
from ..utils import delete_upload, save_avatar
from . import bp
from .forms import EditProfileForm


def _commit() -> bool:
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos en perfiles")
        flash("No se pudo guardar. Inténtalo de nuevo.", "error")
        return False
    return True


@bp.route("/<username>")
def view(username):
    user = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    page = request.args.get("page", 1, type=int)
    stmt = (db.select(Post).options(joinedload(Post.author), selectinload(Post.tags))
            .where(Post.user_id == user.id).order_by(Post.created_at.desc()))
    pagination = db.paginate(stmt, page=page,
                             per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
    post_service.attach_stats(pagination.items, current_user)
    following = current_user.is_authenticated and current_user.is_following(user)
    return render_template("profile/view.html", user=user, posts=pagination.items,
                           pagination=pagination, is_following=following,
                           followers=user.follower_count, following_count=user.following_count)


@bp.route("/<username>/follow", methods=["POST"])
@login_required
def follow(username):
    user = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    if user.id == current_user.id:
        flash("No puedes seguirte a ti mismo.", "error")
        return redirect(url_for("profile.view", username=username))
    if not current_user.is_following(user):
        db.session.add(Follow(follower_id=current_user.id, followed_id=user.id))
        Notification.notify(user.id, current_user.id, "follow")
        if _commit():
            flash(f"Ahora sigues a {user.username}.", "success")
    return redirect(url_for("profile.view", username=username))


@bp.route("/<username>/unfollow", methods=["POST"])
@login_required
def unfollow(username):
    user = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    Follow.query.filter_by(follower_id=current_user.id, followed_id=user.id).delete()
    if _commit():
        flash(f"Dejaste de seguir a {user.username}.", "info")
    return redirect(url_for("profile.view", username=username))


@bp.route("/settings/profile", methods=["GET", "POST"])
@login_required
def edit():
    form = EditProfileForm(obj=current_user)
    if form.validate_on_submit():
        try:
            new_avatar = save_avatar(form.avatar.data)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("profile/edit.html", form=form)

        if new_avatar or form.remove_avatar.data:
            delete_upload("avatars", current_user.avatar)
            current_user.avatar = new_avatar  # None si solo se quita

        current_user.bio = (form.bio.data or "").strip()
        current_user.github_url = (form.github_url.data or "").strip() or None
        current_user.twitter_url = (form.twitter_url.data or "").strip() or None
        current_user.website_url = (form.website_url.data or "").strip() or None
        if _commit():
            flash("Perfil actualizado.", "success")
            return redirect(url_for("profile.view", username=current_user.username))
    return render_template("profile/edit.html", form=form)
