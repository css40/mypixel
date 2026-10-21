from flask import (abort, current_app, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..admin.routes import purge_post
from ..extensions import db
from ..models import CodeBlock, Comment, Like, Notification, Post, Rating, Tag
from ..utils import delete_upload, escape_like, parse_tags, save_attachment
from . import bp
from .forms import AdForm, CommentForm, PostForm


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
    post.tags = [Tag.get_or_create(name) for name in parse_tags(form.tags.data)]


# --------------------------------------------------------------------------- #
# Feed
# --------------------------------------------------------------------------- #
@bp.route("/")
def feed():
    page = request.args.get("page", 1, type=int)
    lang = request.args.get("lang", "").strip()
    tag = request.args.get("tag", "").strip()
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "recent")
    following_only = request.args.get("following") == "1" and current_user.is_authenticated

    query = db.select(Post).options(joinedload(Post.author))
    if sort == "top":
        # Media bayesiana: pondera la nota por el número de votos, así un solo
        # voto de 5 estrellas no gana a un post con 20 votos de 4.8.
        cfg = current_app.config
        votes = (db.select(Rating.post_id, func.count(Rating.id).label("n"),
                           func.sum(Rating.score).label("s"))
                 .group_by(Rating.post_id).subquery())
        n = func.coalesce(votes.c.n, 0)
        weighted = ((cfg["RATING_PRIOR_VOTES"] * cfg["RATING_PRIOR_MEAN"] + func.coalesce(votes.c.s, 0))
                    / (cfg["RATING_PRIOR_VOTES"] + n))
        query = (query.outerjoin(votes, votes.c.post_id == Post.id)
                 .order_by(weighted.desc(), n.desc(), Post.created_at.desc()))
    else:
        sort = "recent"
        query = query.order_by(Post.created_at.desc())

    if lang:
        query = query.where(Post.language == lang)
    if tag:
        query = query.join(Post.tags).where(Tag.slug == tag)
    if following_only:
        from ..models import Follow
        following_ids = [r[0] for r in db.session.query(Follow.followed_id)
                        .filter(Follow.follower_id == current_user.id)]
        query = query.where(Post.user_id.in_(following_ids or [-1]))
    if q:
        term = f"%{escape_like(q)}%"
        query = query.where(or_(Post.title.ilike(term, escape="\\"),
                                Post.body.ilike(term, escape="\\")))

    # Sin filtros, la publicidad va fijada arriba (para todos, admin incluido) y no se repite en la lista.
    pinned_ads = []
    if not (lang or tag or q or following_only):
        query = query.where(Post.is_ad.is_(False))
        if page == 1:
            pinned_ads = (Post.query.options(joinedload(Post.author)).filter(Post.is_ad.is_(True))
                          .order_by(Post.created_at.desc()).limit(3).all())

    pagination = db.paginate(query, page=page,
                             per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)

    liked_ids = set()
    if current_user.is_authenticated and pagination.items:
        ids = [p.id for p in pagination.items]
        liked_ids = {row.post_id for row in
                     Like.query.filter(Like.user_id == current_user.id, Like.post_id.in_(ids))}

    counts = dict(db.session.query(Post.language, func.count(Post.id))
                  .group_by(Post.language).all())
    popular_tags = (db.session.query(Tag, func.count(Post.id).label("n"))
                    .join(Tag.posts).group_by(Tag.id).order_by(func.count(Post.id).desc())
                    .limit(15).all())

    return render_template("posts/feed.html", pagination=pagination, posts=pagination.items,
                           active_lang=lang, active_tag=tag, q=q, sort=sort,
                           following_only=following_only, liked_ids=liked_ids, pinned_ads=pinned_ads,
                           lang_counts=counts, popular_tags=popular_tags)


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
        db.session.commit()
        flash("Publicación creada.", "success")
        return redirect(url_for("posts.detail", post_id=post.id))
    return render_template("posts/form.html", form=form, editing=False)


# --------------------------------------------------------------------------- #
# Publicaciones especiales (publicidad): solo el administrador
# --------------------------------------------------------------------------- #
AD_CATEGORY = "Anuncio"


def _require_admin():
    if not current_user.is_admin:
        abort(403)


def _ad_image(form, required):
    """Valida y sube la imagen del anuncio. Devuelve (url, nombre) o (None, None) si no hay."""
    file = form.image.data
    if not getattr(file, "filename", ""):
        if required:
            raise ValueError("El anuncio necesita una imagen.")
        return None, None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        raise ValueError("La imagen debe ser PNG, JPG, GIF o WEBP.")
    return save_attachment(file)


@bp.route("/posts/ad/new", methods=["GET", "POST"])
@login_required
def create_ad():
    _require_admin()
    form = AdForm()
    if form.validate_on_submit():
        try:
            stored, original = _ad_image(form, required=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("posts/ad_form.html", form=form, editing=False)
        post = Post(title=form.title.data.strip(), language=AD_CATEGORY, is_ad=True,
                    body=form.body.data.strip(), attachment=stored,
                    attachment_name=original, author=current_user)
        db.session.add(post)
        db.session.commit()
        flash("Anuncio publicado.", "success")
        return redirect(url_for("posts.feed"))
    return render_template("posts/ad_form.html", form=form, editing=False)


@bp.route("/posts/ad/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_ad(post_id):
    _require_admin()
    post = db.get_or_404(Post, post_id)
    if not post.is_ad:
        return redirect(url_for("posts.edit", post_id=post.id))
    form = AdForm(obj=post)
    if form.validate_on_submit():
        try:
            stored, original = _ad_image(form, required=False)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("posts/ad_form.html", form=form, editing=True, post=post)
        if stored:
            delete_upload("attachments", post.attachment)
            post.attachment, post.attachment_name = stored, original
        post.title = form.title.data.strip()
        post.body = form.body.data.strip()
        db.session.commit()
        flash("Anuncio actualizado.", "success")
        return redirect(url_for("posts.feed"))
    return render_template("posts/ad_form.html", form=form, editing=True, post=post)


@bp.route("/posts/<int:post_id>")
def detail(post_id):
    post = db.get_or_404(Post, post_id)
    comments = post.comments.options(joinedload(Comment.author)).all()
    return render_template("posts/detail.html", post=post, comments=comments,
                           comment_form=CommentForm(), liked=post.is_liked_by(current_user),
                           my_rating=post.rating_by(current_user))


@bp.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit(post_id):
    post = db.get_or_404(Post, post_id)
    if post.is_ad:
        return redirect(url_for("posts.edit_ad", post_id=post.id))
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
        if stored:
            delete_upload("attachments", post.attachment)
            post.attachment, post.attachment_name = stored, original
        post.title = form.title.data.strip()
        post.language = form.language.data
        post.body = form.body.data.strip()
        _apply_code_blocks(post, form)
        _apply_tags(post, form)
        db.session.commit()
        flash("Cambios guardados.", "success")
        return redirect(url_for("posts.detail", post_id=post.id))
    return render_template("posts/form.html", form=form, editing=True, post=post)


@bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@login_required
def delete(post_id):
    post = db.get_or_404(Post, post_id)
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    purge_post(post)
    db.session.commit()
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
        db.session.commit()
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
    db.session.commit()
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
    if post.is_ad:
        return jsonify(error="Los anuncios no se pueden valorar."), 400

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
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="No se pudo guardar tu valoración, inténtalo de nuevo."), 409

    stats = post.rating_stats()
    return jsonify(my_rating=my, count=stats[0], avg=stats[1], featured=post.is_featured(stats))
