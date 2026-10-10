from flask import (abort, current_app, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import CodeBlock, Comment, Like, Notification, Post, Rating, Tag
from ..services import post_service
from ..utils import delete_upload, parse_tags, save_attachment
from . import bp
from .forms import CommentForm, PostForm


def _commit_or_fail(message="No se pudo guardar. Inténtalo de nuevo.") -> bool:
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos en posts")
        flash(message, "error")
        return False
    post_service.invalidate_sidebar_cache()
    return True


def _apply_code_blocks(post: Post, form: PostForm) -> None:
    post.code_blocks.clear()
    for i, field in enumerate(form.filled_code_blocks()):
        post.code_blocks.append(CodeBlock(
            label=(field.form.label.data or "").strip()[:60] or None,
            language=field.form.language.data,
            code=field.form.code.data.rstrip(),
            position=i,
        ))


def _apply_tags(post: Post, form: PostForm) -> None:
    unique = {}                       # dos nombres distintos pueden dar el mismo slug
    for name in parse_tags(form.tags.data):
        tag = Tag.get_or_create(name)
        unique[tag.slug] = tag
    post.tags = list(unique.values())


# --------------------------------------------------------------------------- #
# Feed
# --------------------------------------------------------------------------- #
@bp.route("/")
def feed():
    page = request.args.get("page", 1, type=int)
    lang = request.args.get("lang", "").strip()
    tag = request.args.get("tag", "").strip()
    q = request.args.get("q", "").strip()
    sort = "top" if request.args.get("sort") == "top" else "recent"
    following_only = request.args.get("following") == "1" and current_user.is_authenticated

    query = post_service.feed_query(
        sort=sort, lang=lang, tag=tag, q=q,
        following_of=current_user.id if following_only else None)
    pagination = db.paginate(query, page=page,
                             per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
    post_service.attach_stats(pagination.items, current_user)

    return render_template("posts/feed.html", pagination=pagination, posts=pagination.items,
                           active_lang=lang, active_tag=tag, q=q, sort=sort,
                           following_only=following_only,
                           lang_counts=post_service.language_counts(),
                           popular_tags=post_service.popular_tags())


# --------------------------------------------------------------------------- #
# CRUD de posts
# --------------------------------------------------------------------------- #
@bp.route("/posts/new", methods=["GET", "POST"])
@login_required
def create():
    form = PostForm()
    if form.validate_on_submit():
        try:
            stored, original = save_attachment(form.attachment.data)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("posts/form.html", form=form, editing=False)
        post = Post(title=form.title.data.strip(), language=form.language.data,
                    body=form.body.data.strip(), attachment=stored,
                    attachment_name=original, author=current_user)
        db.session.add(post)
        _apply_code_blocks(post, form)
        _apply_tags(post, form)
        if _commit_or_fail():
            flash("Publicación creada.", "success")
            return redirect(url_for("posts.detail", post_id=post.id))
        delete_upload("attachments", stored)
    return render_template("posts/form.html", form=form, editing=False)


@bp.route("/posts/<int:post_id>")
def detail(post_id):
    post = db.get_or_404(Post, post_id)
    comments = post.comments.options(joinedload(Comment.author)).all()
    post_service.attach_stats([post], current_user)
    return render_template("posts/detail.html", post=post, comments=comments,
                           comment_form=CommentForm(), my_rating=post.rating_by(current_user))


@bp.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit(post_id):
    post = db.get_or_404(Post, post_id)
    if post.user_id != current_user.id:
        abort(403)
    form = PostForm(obj=post)
    if request.method == "GET":
        form.tags.data = ", ".join(t.name for t in post.tags)
        # precargar los bloques de código existentes en el FieldList
        while len(form.code_blocks) < max(len(post.code_blocks), 1):
            form.code_blocks.append_entry()
        for field, block in zip(form.code_blocks, post.code_blocks):
            field.form.label.data, field.form.language.data, field.form.code.data = (
                block.label, block.language, block.code)

    if form.validate_on_submit():
        try:
            stored, original = save_attachment(form.attachment.data)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("posts/form.html", form=form, editing=True, post=post)
        old_attachment = post.attachment
        if stored:
            post.attachment, post.attachment_name = stored, original
        post.title = form.title.data.strip()
        post.language = form.language.data
        post.body = form.body.data.strip()
        _apply_code_blocks(post, form)
        _apply_tags(post, form)
        if _commit_or_fail():
            if stored:
                delete_upload("attachments", old_attachment)   # el archivo viejo ya no se usa
            flash("Cambios guardados.", "success")
            return redirect(url_for("posts.detail", post_id=post.id))
        delete_upload("attachments", stored)
    return render_template("posts/form.html", form=form, editing=True, post=post)


@bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@login_required
def delete(post_id):
    post = db.get_or_404(Post, post_id)
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    attachment = post.attachment
    db.session.delete(post)
    if _commit_or_fail():
        delete_upload("attachments", attachment)   # solo se borra el archivo si la BD confirmó
        flash("Publicación eliminada.", "info")
    return redirect(url_for("posts.feed"))


# --------------------------------------------------------------------------- #
# Comentarios
# --------------------------------------------------------------------------- #
@bp.route("/posts/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = db.get_or_404(Post, post_id)
    form = CommentForm()
    if form.validate_on_submit():
        db.session.add(Comment(body=form.body.data.strip(), author=current_user, post=post))
        Notification.notify(post.user_id, current_user.id, "comment", post.id)
        if _commit_or_fail():
            flash("Comentario publicado.", "success")
    else:
        flash("El comentario no puede estar vacío (máx. 2000 caracteres).", "error")
    return redirect(url_for("posts.detail", post_id=post.id) + "#comments")


@bp.route("/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = db.get_or_404(Comment, comment_id)
    if not current_user.is_admin and current_user.id not in (comment.user_id, comment.post.user_id):
        abort(403)
    post_id = comment.post_id
    db.session.delete(comment)
    if _commit_or_fail():
        flash("Comentario eliminado.", "info")
    return redirect(url_for("posts.detail", post_id=post_id) + "#comments")


# --------------------------------------------------------------------------- #
# Likes (AJAX)
# --------------------------------------------------------------------------- #
@bp.route("/posts/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    post = db.get_or_404(Post, post_id)
    like = Like.query.filter_by(user_id=current_user.id, post_id=post.id).first()
    try:
        if like:
            db.session.delete(like)
            liked = False
        else:
            db.session.add(Like(user_id=current_user.id, post_id=post.id))
            Notification.notify(post.user_id, current_user.id, "like", post.id)
            liked = True
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        liked = True
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error al guardar un me gusta")
        return jsonify(error="No se pudo guardar tu me gusta."), 503
    return jsonify(liked=liked, count=post.like_count)


# --------------------------------------------------------------------------- #
# Valoraciones (AJAX, 1-5 estrellas): más valoraciones y mejor media => "recomendado"
# --------------------------------------------------------------------------- #
@bp.route("/posts/<int:post_id>/rate", methods=["POST"])
@login_required
def rate(post_id):
    post = db.get_or_404(Post, post_id)
    if post.user_id == current_user.id:
        return jsonify(error="No puedes valorar tu propio código."), 403

    score = request.form.get("score", type=int)
    if score not in (1, 2, 3, 4, 5):
        return jsonify(error="La valoración debe ser de 1 a 5."), 400

    rating = Rating.query.filter_by(user_id=current_user.id, post_id=post.id).first()
    if rating and rating.score == score:       # pulsar la misma nota otra vez la quita
        db.session.delete(rating)
        my = None
    elif rating:
        rating.score = score
        my = score
    else:
        db.session.add(Rating(user_id=current_user.id, post_id=post.id, score=score))
        Notification.notify(post.user_id, current_user.id, "rating", post.id)
        my = score
    try:
        db.session.commit()
    except (IntegrityError, SQLAlchemyError):
        db.session.rollback()
        return jsonify(error="No se pudo guardar tu valoración, inténtalo de nuevo."), 409

    stats = post.rating_stats()
    return jsonify(my_rating=my, count=stats[0], avg=stats[1], featured=post.is_featured(stats))
