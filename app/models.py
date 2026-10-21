"""Modelos SQLAlchemy.

Resumen de relaciones
----------------------
User 1—N Post 1—N Comment
User 1—N Like N—1 Post          (única por usuario+post)
User 1—N Rating N—1 Post        (única por usuario+post, 1-5 estrellas)
Post 1—N CodeBlock              (varios snippets por publicación, ordenados)
Post N—N Tag                    (tabla puente post_tags)
User N—N User (Follow)          (seguidor / seguido)
User 1—N Notification           (avisos: comentario, like, valoración, seguidor, mensaje, reportes)
Report                          (denuncia de una publicación o de un perfil, la resuelve el admin)
User 1—N ReadMarker             (última lectura por conversación de chat, persistente)
User 1—N Message (sender) / User 1—N Message (recipient)  -> chat privado
Group 1—N GroupMember N—1 User  -> membresías de grupos
Group 1—N Message               -> chat de grupo
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import current_app, url_for
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, func
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
    is_verified = db.Column(db.Boolean, default=False, nullable=False)

    bio = db.Column(db.Text, default="", nullable=False)
    avatar = db.Column(db.Text)                   # URL de ImgBB o nombre local antiguo
    avatar_emoji = db.Column(db.String(16))       # avatar retro (emoji) si no hay imagen
    avatar_color = db.Column(db.String(9))        # color de fondo del avatar retro (#rrggbb)
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
            if urlsplit(self.avatar).scheme in {"http", "https"}:
                return self.avatar
            return url_for("main.uploaded_file", filename=f"avatars/{self.avatar}")
        return None

    @property
    def initial(self) -> str:
        return (self.username or "?")[:1].upper()

    # -- avatar retro: si el usuario no eligió nada, se deduce de su nombre (estable)
    @property
    def display_emoji(self) -> str:
        options = current_app.config["AVATAR_EMOJIS"]
        return self.avatar_emoji or options[len(self.username or "") % len(options)]

    @property
    def display_color(self) -> str:
        options = current_app.config["AVATAR_COLORS"]
        return self.avatar_color or options[len(self.username or "") % len(options)]

    # -- seguidores
    def is_following(self, other) -> bool:
        if not other or not getattr(other, "id", None):
            return False
        return Follow.query.filter_by(follower_id=self.id, followed_id=other.id).count() > 0

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

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    language = db.Column(db.String(30), nullable=False, index=True)  # categoría principal
    body = db.Column(db.Text, nullable=False)                        # Markdown
    attachment = db.Column(db.Text)                 # URL de ImgBB o nombre local antiguo
    attachment_name = db.Column(db.String(255))     # nombre original
    # Publicación especial (publicidad) creada solo por el admin: imagen + título + descripción,
    # se muestra completa en el inicio. La imagen se guarda en `attachment` y el texto en `body`.
    is_ad = db.Column(db.Boolean, default=False, nullable=False)
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
        path = urlsplit(self.attachment).path
        ext = path.rsplit(".", 1)[-1].lower()
        return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]

    @property
    def attachment_url(self) -> str | None:
        if not self.attachment:
            return None
        if urlsplit(self.attachment).scheme in {"http", "https"}:
            return self.attachment
        return url_for("main.uploaded_file", filename=f"attachments/{self.attachment}")

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
    """Aviso persistente.

    kind: comment | like | rating | follow | message | report_result | warning
    `message` guarda un texto libre (vista previa del mensaje, o el detalle de la decisión
    del admin en un reporte)."""
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)  # receptor
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)              # quien la causó
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"))
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="notifications")
    actor = db.relationship("User", foreign_keys=[actor_id])
    post = db.relationship("Post")

    @staticmethod
    def notify(user_id, actor_id, kind, post_id=None, message=None):
        """Crea la notificación salvo que sea sobre uno mismo (no te avisas a ti mismo)."""
        if user_id == actor_id:
            return None
        n = Notification(user_id=user_id, actor_id=actor_id, kind=kind,
                         post_id=post_id, message=message)
        db.session.add(n)
        return n

    @staticmethod
    def notify_message(user_id, actor_id, preview):
        """Aviso de mensaje directo nuevo. Mientras no lo leas, los mensajes siguientes de la
        misma persona se agrupan en un único aviso (se actualiza la vista previa y la hora)."""
        existing = Notification.query.filter_by(user_id=user_id, actor_id=actor_id,
                                                kind="message", read_at=None).first()
        if existing:
            existing.message = preview
            existing.created_at = utcnow()
            return existing
        return Notification.notify(user_id, actor_id, "message", message=preview)

    @staticmethod
    def mark_dm_read(user_id, actor_id):
        """Marca como leídos los avisos de mensajes que `actor_id` envió a `user_id`."""
        Notification.query.filter_by(user_id=user_id, actor_id=actor_id,
                                     kind="message", read_at=None).update(
            {"read_at": utcnow()}, synchronize_session=False)


# --------------------------------------------------------------------------- #
# Reportes (denuncias de publicaciones o perfiles)
# --------------------------------------------------------------------------- #
class Report(db.Model):
    """Un usuario denuncia una publicación o un perfil; el admin la revisa y decide."""
    __tablename__ = "reports"

    REASONS = [
        ("spam", "Spam o publicidad no deseada"),
        ("abuso", "Acoso o lenguaje ofensivo"),
        ("inapropiado", "Contenido inapropiado"),
        ("suplantacion", "Suplantación de identidad"),
        ("malicioso", "Código malicioso o estafa"),
        ("otro", "Otro motivo"),
    ]
    # decisión del admin -> texto que se muestra
    DECISIONS = {
        "dismiss": "Sin acción: no se encontró ninguna infracción",
        "warn": "Advertencia al usuario",
        "delete_post": "Publicación eliminada",
        "ban": "Usuario suspendido",
    }

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)             # post | profile
    reason = db.Column(db.String(20), nullable=False)
    details = db.Column(db.Text, default="", nullable=False)
    status = db.Column(db.String(10), default="pending", nullable=False, index=True)  # pending | resolved
    decision = db.Column(db.String(20))
    admin_note = db.Column(db.Text)
    target_label = db.Column(db.String(150), nullable=False, default="")  # título o @usuario (se conserva si se borra)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    resolved_at = db.Column(db.DateTime)

    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    reported_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"))
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reported_user = db.relationship("User", foreign_keys=[reported_user_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    post = db.relationship("Post")

    @property
    def reason_label(self) -> str:
        return dict(self.REASONS).get(self.reason, self.reason)

    @property
    def decision_label(self) -> str:
        return self.DECISIONS.get(self.decision, "")


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
        return GroupMember.query.filter_by(group_id=self.id, user_id=user.id).first() is not None

    @property
    def member_count(self) -> int:
        return len(self.members)


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
            "sender_emoji": self.sender.display_emoji,
            "sender_color": self.sender.display_color,
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
    def mark_read(user_id: int, conv_key: str) -> None:
        marker = ReadMarker.query.filter_by(user_id=user_id, conv_key=conv_key).first()
        if marker:
            marker.last_read_at = utcnow()
        else:
            db.session.add(ReadMarker(user_id=user_id, conv_key=conv_key, last_read_at=utcnow()))
        db.session.commit()


def dm_room(user_a_id: int, user_b_id: int) -> str:
    """Nombre de sala determinista para un chat directo entre dos usuarios."""
    low, high = sorted((int(user_a_id), int(user_b_id)))
    return f"dm_{low}_{high}"
