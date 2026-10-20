from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import Follow, Notification, Post, User
from ..utils import delete_upload, save_avatar
from . import bp
from .forms import EditProfileForm


@bp.route("/<username>")
def view(username):
    user = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    page = request.args.get("page", 1, type=int)
    stmt = db.select(Post).where(Post.user_id == user.id).order_by(Post.created_at.desc())
    pagination = db.paginate(stmt, page=page,
                             per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
    return render_template("profile/view.html", user=user, posts=pagination.items,
                           pagination=pagination,
                           is_following=current_user.is_following(user))


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
        db.session.commit()
        flash(f"Ahora sigues a {user.username}.", "success")
    return redirect(url_for("profile.view", username=username))


@bp.route("/<username>/unfollow", methods=["POST"])
@login_required
def unfollow(username):
    user = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    Follow.query.filter_by(follower_id=current_user.id, followed_id=user.id).delete()
    db.session.commit()
    flash(f"Dejaste de seguir a {user.username}.", "info")
    return redirect(url_for("profile.view", username=username))


@bp.route("/settings/profile", methods=["GET", "POST"])
@login_required
def edit():
    cfg = current_app.config
    form = EditProfileForm(obj=current_user)
    if request.method == "GET":
        # Qué opción aparece marcada y qué avatar retro hay seleccionado ahora mismo
        form.avatar_mode.data = "image" if current_user.avatar else "emoji"
        form.avatar_emoji.data = current_user.display_emoji
        form.avatar_color.data = current_user.display_color

    if form.validate_on_submit():
        mode = form.avatar_mode.data or "emoji"
        emoji, color = (form.avatar_emoji.data or "").strip(), (form.avatar_color.data or "").strip()
        # Solo se aceptan los emojis/colores de la lista (nada de HTML ni valores raros).
        if emoji not in cfg["AVATAR_EMOJIS"]:
            emoji = current_user.display_emoji
        if color not in cfg["AVATAR_COLORS"]:
            color = current_user.display_color

        if mode == "image":
            try:
                new_avatar = save_avatar(form.avatar.data)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("profile/edit.html", form=form)
            if new_avatar:
                delete_upload("avatars", current_user.avatar)
                current_user.avatar = new_avatar
            elif not current_user.avatar:
                flash("Elige una imagen para usarla como avatar o cambia a un avatar retro.", "error")
                return render_template("profile/edit.html", form=form)
        else:
            # Avatar retro: se quita la imagen propia (si tenía una)
            if current_user.avatar:
                delete_upload("avatars", current_user.avatar)
                current_user.avatar = None

        # Siempre se guarda la elección retro, para que vuelva a estar ahí si quita su imagen.
        current_user.avatar_emoji = emoji
        current_user.avatar_color = color
        current_user.bio = (form.bio.data or "").strip()
        current_user.github_url = (form.github_url.data or "").strip() or None
        current_user.twitter_url = (form.twitter_url.data or "").strip() or None
        current_user.website_url = (form.website_url.data or "").strip() or None
        db.session.commit()
        flash("Perfil actualizado.", "success")
        return redirect(url_for("profile.view", username=current_user.username))
    return render_template("profile/edit.html", form=form)
