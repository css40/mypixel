"""Tareas de base de datos que se ejecutan al arrancar: índices y cuenta admin."""
from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .extensions import db

# (nombre, tabla, columnas). `create_all()` solo crea tablas nuevas, así que los
# índices añadidos después se crean aquí de forma idempotente (IF NOT EXISTS
# funciona igual en SQLite y PostgreSQL).
INDEXES = [
    ("ix_messages_group_created", "messages", "group_id, created_at"),
    ("ix_messages_dm_pair", "messages", "sender_id, recipient_id, created_at"),
    ("ix_messages_recipient_sender", "messages", "recipient_id, sender_id, created_at"),
    ("ix_notifications_user_read", "notifications", "user_id, read_at"),
    ("ix_posts_user_created", "posts", "user_id, created_at"),
    ("ix_follows_follower", "follows", "follower_id"),
    ("ix_group_members_user", "group_members", "user_id"),
]


def ensure_indexes(app: Flask) -> None:
    for name, table, columns in INDEXES:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
        except SQLAlchemyError as exc:
            app.logger.warning("No se pudo crear el índice %s: %s", name, exc)


def ensure_admin(app: Flask) -> None:
    """Crea la cuenta de administrador si todavía no hay ningún admin."""
    from .models import User

    cfg = app.config
    if not (cfg.get("ADMIN_EMAIL") and cfg.get("ADMIN_USERNAME") and cfg.get("ADMIN_PASSWORD")):
        return
    try:
        if db.session.query(User.id).filter_by(is_admin=True).first():
            return
        email = cfg["ADMIN_EMAIL"].lower()
        admin = User.query.filter_by(email=email).first() or User(
            username=cfg["ADMIN_USERNAME"], email=email
        )
        admin.is_admin = True
        admin.set_password(cfg["ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
        app.logger.warning("Cuenta admin creada: %s", email)
    except IntegrityError:
        db.session.rollback()   # otro proceso la creó a la vez
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Error al crear la cuenta admin")


def init_database(app: Flask) -> None:
    if not app.config.get("AUTO_CREATE_DB", True):
        return
    try:
        db.create_all()
    except SQLAlchemyError:
        # Varios procesos arrancando a la vez pueden competir por crear las tablas.
        db.session.rollback()
        app.logger.warning("create_all() falló; probablemente otro proceso ya creó las tablas.")
    ensure_indexes(app)
    ensure_admin(app)
