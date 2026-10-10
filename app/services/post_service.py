"""Lógica de publicaciones: consulta del feed, contadores agregados y cachés pequeños."""
import threading
import time
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import CodeBlock, Comment, Follow, Like, Post, Rating, Tag, post_tags
from ..utils import escape_like


# --------------------------------------------------------------------------- #
# Contadores de una página de posts (evita el N+1 del feed y de los perfiles)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PostStats:
    likes: int = 0
    comments: int = 0
    code_blocks: int = 0
    rating_count: int = 0
    rating_avg: float = 0.0
    liked: bool = False
    featured: bool = False


def _count_by_post(model, ids):
    rows = db.session.execute(
        select(model.post_id, func.count()).where(model.post_id.in_(ids))
        .group_by(model.post_id)).all()
    return dict(rows)


def attach_stats(posts, user=None) -> None:
    """Añade `post.stats` (PostStats) a cada post con 5 consultas agrupadas en total,
    en lugar de 5-6 consultas por post."""
    posts = list(posts)
    if not posts:
        return
    ids = [p.id for p in posts]
    likes = _count_by_post(Like, ids)
    comments = _count_by_post(Comment, ids)
    blocks = _count_by_post(CodeBlock, ids)
    ratings = {pid: (n, avg) for pid, n, avg in db.session.execute(
        select(Rating.post_id, func.count(), func.avg(Rating.score))
        .where(Rating.post_id.in_(ids)).group_by(Rating.post_id)).all()}
    liked = set()
    if user is not None and user.is_authenticated:
        liked = set(db.session.execute(
            select(Like.post_id).where(Like.user_id == user.id, Like.post_id.in_(ids))).scalars())

    cfg = current_app.config
    for p in posts:
        n, avg = ratings.get(p.id, (0, None))
        avg = round(float(avg), 1) if avg is not None else 0.0
        p.stats = PostStats(
            likes=likes.get(p.id, 0), comments=comments.get(p.id, 0),
            code_blocks=blocks.get(p.id, 0), rating_count=int(n), rating_avg=avg,
            liked=p.id in liked,
            featured=n >= cfg["FEATURED_MIN_RATINGS"] and avg >= cfg["FEATURED_MIN_AVG"],
        )


# --------------------------------------------------------------------------- #
# Consulta del feed
# --------------------------------------------------------------------------- #
def feed_query(*, sort="recent", lang="", tag="", q="", following_of=None):
    """Construye el SELECT del feed. `following_of` es el id del usuario cuyas
    cuentas seguidas se quieren ver (o None)."""
    query = (select(Post)
             .options(joinedload(Post.author), selectinload(Post.tags)))

    if sort == "top":
        # Media bayesiana: pondera la nota por el número de votos, así un solo voto
        # de 5 estrellas no gana a un post con 20 votos de 4.8.
        cfg = current_app.config
        votes = (select(Rating.post_id, func.count(Rating.id).label("n"),
                        func.sum(Rating.score).label("s"))
                 .group_by(Rating.post_id).subquery())
        n = func.coalesce(votes.c.n, 0)
        weighted = ((cfg["RATING_PRIOR_VOTES"] * cfg["RATING_PRIOR_MEAN"]
                     + func.coalesce(votes.c.s, 0)) / (cfg["RATING_PRIOR_VOTES"] + n))
        query = (query.outerjoin(votes, votes.c.post_id == Post.id)
                 .order_by(weighted.desc(), n.desc(), Post.created_at.desc()))
    else:
        query = query.order_by(Post.created_at.desc())

    if lang:
        query = query.where(Post.language == lang)
    if tag:
        query = query.where(Post.id.in_(
            select(post_tags.c.post_id).join(Tag, Tag.id == post_tags.c.tag_id)
            .where(Tag.slug == tag)))
    if following_of is not None:
        query = query.where(Post.user_id.in_(
            select(Follow.followed_id).where(Follow.follower_id == following_of)))
    if q:
        term = f"%{escape_like(q)}%"
        query = query.where(or_(Post.title.ilike(term, escape="\\"),
                                Post.body.ilike(term, escape="\\")))
    return query


# --------------------------------------------------------------------------- #
# Agregados de la barra lateral con caché de vida corta
# --------------------------------------------------------------------------- #
_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key, loader):
    ttl = current_app.config["SIDEBAR_CACHE_SECONDS"]
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def invalidate_sidebar_cache() -> None:
    with _cache_lock:
        _cache.clear()


def language_counts() -> dict:
    return _cached("langs", lambda: dict(
        db.session.execute(select(Post.language, func.count()).group_by(Post.language)).all()))


def popular_tags(limit=15) -> list:
    """Lista de dicts simples (no objetos ORM) para poder cachearlos entre peticiones."""
    def load():
        n = func.count(post_tags.c.post_id)
        rows = db.session.execute(
            select(Tag.slug, Tag.name, n.label("n"))
            .join(post_tags, post_tags.c.tag_id == Tag.id)
            .group_by(Tag.id, Tag.slug, Tag.name).order_by(desc(n)).limit(limit)).all()
        return [{"slug": r.slug, "name": r.name, "n": r.n} for r in rows]
    return _cached(f"tags{limit}", load)
