"""Modelos SQLAlchemy.

Resumen de relaciones
----------------------
User 1—N Post 1—N Comment
User 1—N Like N—1 Post          (única por usuario+post)
User 1—N Rating N—1 Post        (única por usuario+post, 1-5 estrellas)
Post 1—N CodeBlock              (varios snippets por publicación, ordenados)
Post N—N Tag                    (tabla puente post_tags)
User N—N User (Follow)          (seguidor / seguido)
User 1—N Notification           (avisos: comentario, like, valoración, seguidor)
User 1—N ReadMarker             (última lectura por conversación de chat, persistente)
User 1—N Message (sender) / User 1—N Message (recipient)  -> chat privado
Group 1—N GroupMember N—1 User  -> membresías de grupos
Group 1—N Message               -> chat de grupo
"""
from datetime import datetime, timezone

from flask import current_app, url_for
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, func, update
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def utcnow() -> datetime:
    # SQLite guarda datetimes sin zona; almacenamos siempre UTC "naive".
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Seguidores (tabla de asociación simple, con timestamp -> modelo propio)
# --------------------------------------------------------------------------- #
class Follow(db.Model):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "followed_id", name="uq_follow"),
                      CheckConstraint("follower_id != followed_id", name="ck_no_self_follow"))

    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- #
# Usuarios
# --------------------------------------------------------------------------- #
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)

    bio = db.Column(db.Text, default="", nullable=False)
    avatar = db.Column(db.String(255))            # nombre de archivo en uploads/avatars
    github_url = db.Column(db.String(255))
    twitter_url = db.Column(db.String(255))
    website_url = db.Column(db.String(255))
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_banned = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    posts = db.relationship("Post", back_populates="author",
                            cascade="all, delete-orphan", lazy="dynamic")
    comments = db.relationship("Comment", back_populates="author",
                               cascade="all, delete-orphan", lazy="dynamic")
    likes = db.relationship("Like", back_populates="user",
                            cascade="all, delete-orphan", lazy="dynamic")
    ratings = db.relationship("Rating", back_populates="user",
                              cascade="all, delete-orphan", lazy="dynamic")
    memberships = db.relationship("GroupMember", back_populates="user",
                                  cascade="all, delete-orphan")
    sent_messages = db.relationship("Message", foreign_keys="Message.sender_id",
                                    back_populates="sender",
                                    cascade="all, delete-orphan", lazy="dynamic")
    received_messages = db.relationship("Message", foreign_keys="Message.recipient_id",
                                        back_populates="recipient", lazy="dynamic")
    notifications = db.relationship("Notification", foreign_keys="Notification.user_id",
                                    back_populates="user", cascade="all, delete-orphan",
                                    lazy="dynamic")

    # -- contraseñas
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # -- Flask-Login: una cuenta baneada no puede iniciar/mantener sesión
    @property
    def is_active(self):
        return not self.is_banned

    # -- helpers
    @property
    def avatar_url(self):
        if self.avatar:
            return url_for("main.uploaded_file", filename=f"avatars/{self.avatar}")
        return None

    @property
    def initial(self) -> str:
        return (self.username or "?")[:1].upper()

    # -- seguidores
    def is_following(self, other) -> bool:
        if not other or not getattr(other, "id", None):
            return False
        return db.session.query(Follow.id).filter_by(
            follower_id=self.id, followed_id=other.id).first() is not None

    @property
    def follower_count(self) -> int:
        return Follow.query.filter_by(followed_id=self.id).count()

    @property
    def following_count(self) -> int:
        return Follow.query.filter_by(follower_id=self.id).count()

    @property
    def unread_notifications_count(self) -> int:
        return self.notifications.filter_by(read_at=None).count()

    def __repr__(self) -> str:
        return f"<User {self.username}>"


@login_manager.user_loader
def load_user(user_id: str):
    user = db.session.get(User, int(user_id))
    # Si banean a alguien con la sesión abierta, se le cierra en la siguiente petición.
    return user if user and not user.is_banned else None


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #
post_tags = db.Table(
    "post_tags",
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id"), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False)

    posts = db.relationship("Post", secondary=post_tags, back_populates="tags")

    @staticmethod
    def get_or_create(raw_name: str) -> "Tag":
        name = raw_name.strip()[:40]
        slug = "-".join(name.lower().split())
        tag = Tag.query.filter_by(slug=slug).first()
        if not tag:
            tag = Tag(slug=slug, name=name)
            db.session.add(tag)
        return tag


# --------------------------------------------------------------------------- #
# Publicaciones
# --------------------------------------------------------------------------- #
class Post(db.Model):
    __tablename__ = "posts"
    __table_args__ = (Index("ix_posts_user_created", "user_id", "created_at"),)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    language = db.Column(db.String(30), nullable=False, index=True)  # categoría principal
    body = db.Column(db.Text, nullable=False)                        # Markdown
    attachment = db.Column(db.String(255))          # nombre guardado en disco
    attachment_name = db.Column(db.String(255))     # nombre original
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    author = db.relationship("User", back_populates="posts")
    comments = db.relationship("Comment", back_populates="post",
                               cascade="all, delete-orphan", lazy="dynamic",
                               order_by="Comment.created_at")
    likes = db.relationship("Like", back_populates="post",
                            cascade="all, delete-orphan", lazy="dynamic")
    ratings = db.relationship("Rating", back_populates="post",
                              cascade="all, delete-orphan", lazy="dynamic")
    code_blocks = db.relationship("CodeBlock", back_populates="post",
                                  cascade="all, delete-orphan", order_by="CodeBlock.position")
    tags = db.relationship("Tag", secondary=post_tags, back_populates="posts")

    # -- valoraciones ------------------------------------------------------
    def rating_stats(self):
        """(numero_de_valoraciones, media). La media es 0.0 si no hay votos."""
        count, avg = (db.session.query(func.count(Rating.id), func.avg(Rating.score))
                      .filter(Rating.post_id == self.id).one())
        return int(count or 0), round(float(avg), 1) if avg is not None else 0.0

    def is_featured(self, stats=None) -> bool:
        """Insignia de 'código recomendado': suficientes votos y buena media."""
        count, avg = stats or self.rating_stats()
        cfg = current_app.config
        return count >= cfg["FEATURED_MIN_RATINGS"] and avg >= cfg["FEATURED_MIN_AVG"]

    def rating_by(self, user):
        if not user.is_authenticated:
            return None
        r = self.ratings.filter_by(user_id=user.id).first()
        return r.score if r else None

    @property
    def like_count(self) -> int:
        return self.likes.count()

    @property
    def comment_count(self) -> int:
        return self.comments.count()

    @property
    def attachment_is_image(self) -> bool:
        if not self.attachment:
            return False
        ext = self.attachment.rsplit(".", 1)[-1].lower()
        return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]

    def is_liked_by(self, user) -> bool:
        if not user.is_authenticated:
            return False
        return self.likes.filter_by(user_id=user.id).count() > 0

    def __repr__(self) -> str:
        return f"<Post {self.id} {self.title!r}>"


class CodeBlock(db.Model):
    """Un post puede llevar varios snippets (p. ej. 'antes' / 'después')."""
    __tablename__ = "code_blocks"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    label = db.Column(db.String(60))            # título opcional, ej. "Antes", "utils.py"
    language = db.Column(db.String(30), nullable=False)
    code = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)

    post = db.relationship("Post", back_populates="code_blocks")

    @property
    def hljs_language(self) -> str:
        mapping = dict(current_app.config["LANGUAGES"])
        return mapping.get(self.language, "plaintext")


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)

    author = db.relationship("User", back_populates="comments")
    post = db.relationship("Post", back_populates="comments")


class Like(db.Model):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_like_user_post"),)

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)

    user = db.relationship("User", back_populates="likes")
    post = db.relationship("Post", back_populates="likes")


class Rating(db.Model):
    """Valoración de 1 a 5 estrellas. Un usuario vota una sola vez por post."""
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="uq_rating_user_post"),
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_rating_score"),
    )

    id = db.Column(db.Integer, primary_key=True)
    score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)

    user = db.relationship("User", back_populates="ratings")
    post = db.relationship("Post", back_populates="ratings")


# --------------------------------------------------------------------------- #
# Notificaciones
# --------------------------------------------------------------------------- #
class Notification(db.Model):
    """Aviso persistente: comentario, like, valoración o nuevo seguidor."""
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)   # comment | like | rating | follow
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)  # receptor
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)              # quien la causó
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="notifications")
    actor = db.relationship("User", foreign_keys=[actor_id])
    post = db.relationship("Post")

    @staticmethod
    def notify(user_id, actor_id, kind, post_id=None):
        """Crea la notificación salvo que sea sobre uno mismo (no te avisas a ti mismo)."""
        if user_id == actor_id:
            return None
        n = Notification(user_id=user_id, actor_id=actor_id, kind=kind, post_id=post_id)
        db.session.add(n)
        return n


# --------------------------------------------------------------------------- #
# Chat: grupos, mensajes y marcadores de lectura
# --------------------------------------------------------------------------- #
class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    description = db.Column(db.String(255), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    owner = db.relationship("User")
    members = db.relationship("GroupMember", back_populates="group",
                              cascade="all, delete-orphan")
    messages = db.relationship("Message", back_populates="group",
                               cascade="all, delete-orphan", lazy="dynamic",
                               order_by="Message.created_at")

    def has_member(self, user) -> bool:
        return db.session.query(GroupMember.id).filter_by(
            group_id=self.id, user_id=user.id).first() is not None

    @property
    def member_count(self) -> int:
        """Usa el valor precalculado (`member_total`) si el servicio lo adjuntó;
        si no, hace un COUNT en lugar de cargar todos los miembros."""
        cached = self.__dict__.get("member_total")
        if cached is not None:
            return cached
        return db.session.query(func.count(GroupMember.id)).filter_by(group_id=self.id).scalar() or 0


class GroupMember(db.Model):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    role = db.Column(db.String(10), default="member", nullable=False)  # admin | member
    joined_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    group = db.relationship("Group", back_populates="members")
    user = db.relationship("User", back_populates="memberships")


class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        # Un mensaje es directo (recipient_id) o de grupo (group_id), nunca ambos.
        CheckConstraint("(recipient_id IS NULL) != (group_id IS NULL)",
                        name="ck_message_single_target"),
        # Historial de grupo y de chats directos (ver services/chat_service.py)
        Index("ix_messages_group_created", "group_id", "created_at"),
        Index("ix_messages_dm_pair", "sender_id", "recipient_id", "created_at"),
        Index("ix_messages_recipient_sender", "recipient_id", "sender_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    is_code = db.Column(db.Boolean, default=False, nullable=False)
    code_language = db.Column(db.String(30))     # alias highlight.js, solo si is_code
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), index=True)

    sender = db.relationship("User", foreign_keys=[sender_id], back_populates="sent_messages")
    recipient = db.relationship("User", foreign_keys=[recipient_id],
                                back_populates="received_messages")
    group = db.relationship("Group", back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "body": self.body,
            "is_code": self.is_code,
            "code_language": self.code_language,
            "sender_id": self.sender_id,
            "sender": self.sender.username,
            "sender_avatar": self.sender.avatar_url,
            "sender_initial": self.sender.initial,
            "created_at": self.created_at.isoformat() + "Z",
        }


class ReadMarker(db.Model):
    """Última vez que un usuario leyó una conversación (para no-leídos persistentes)."""
    __tablename__ = "read_markers"
    __table_args__ = (UniqueConstraint("user_id", "conv_key", name="uq_read_marker"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    conv_key = db.Column(db.String(30), nullable=False)   # 'dm_<id_otro>' o 'group_<id>'
    last_read_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @staticmethod
    def touch(user_id: int, conv_key: str) -> None:
        """Actualiza (o crea) el marcador SIN hacer commit. Un UPDATE directo evita
        cargar el objeto; solo si no existía fila se inserta una nueva."""
        result = db.session.execute(
            update(ReadMarker)
            .where(ReadMarker.user_id == user_id, ReadMarker.conv_key == conv_key)
            .values(last_read_at=utcnow()))
        if result.rowcount == 0:
            db.session.add(ReadMarker(user_id=user_id, conv_key=conv_key, last_read_at=utcnow()))

    @staticmethod
    def mark_read(user_id: int, conv_key: str) -> None:
        ReadMarker.touch(user_id, conv_key)
        db.session.commit()


def dm_room(user_a_id: int, user_b_id: int) -> str:
    """Nombre de sala determinista para un chat directo entre dos usuarios."""
    low, high = sorted((int(user_a_id), int(user_b_id)))
    return f"dm_{low}_{high}"
