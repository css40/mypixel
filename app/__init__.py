"""Application factory."""
import os
from flask import Flask

from config import config_by_name

from .extensions import csrf, db, login_manager, socketio


def create_app(config_name: str = None) -> Flask:
    # Producción = Render (define RENDER=true) o FLASK_ENV=production.
    # OJO: tener DATABASE_URL apuntando a Render desde tu PC NO activa producción,
    # así puedes probar en local (http) contra la base de datos de Render.
    if config_name is None:
        config_name = (
            "production" if (os.environ.get("RENDER") or os.environ.get("FLASK_ENV") == "production")
            else "development"
        )

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Carpetas necesarias
    (app.config["UPLOAD_FOLDER"] / "avatars").mkdir(parents=True, exist_ok=True)
    (app.config["UPLOAD_FOLDER"] / "attachments").mkdir(parents=True, exist_ok=True)

    # Extensiones
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    socketio.init_app(app, cors_allowed_origins=None)

    # Blueprints
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .chat import bp as chat_bp
    from .main import bp as main_bp
    from .posts import bp as posts_bp
    from .profile import bp as profile_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(posts_bp)
    app.register_blueprint(profile_bp, url_prefix="/u")
    app.register_blueprint(chat_bp, url_prefix="/chat")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Registrar eventos de Socket.IO (los decoradores se ejecutan al importar)
    from .chat import events  # noqa: F401

    # Variables disponibles en todas las plantillas
    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        unread = current_user.unread_notifications_count if current_user.is_authenticated else 0
        return {
            "LANGUAGES": app.config["LANGUAGES"],
            "unread_notifications_count": unread,
            "current_user": current_user,
        }
    with app.app_context():
        db.create_all()
        _ensure_columns(app)
        _ensure_url_columns(app)
        _ensure_admin(app)

    return app


def _ensure_columns(app: Flask) -> None:
    """Añade columnas nuevas a bases de datos que ya existían (create_all no las toca).
    Sirve igual para SQLite local y PostgreSQL. En una base nueva no hace nada."""
    from sqlalchemy import inspect, text

    wanted = {"users": {"avatar_emoji": "VARCHAR(16)", "avatar_color": "VARCHAR(9)"}}
    try:
        insp = inspect(db.engine)
        for table, cols in wanted.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    app.logger.warning("Columna añadida: %s.%s", table, name)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - nunca impedir el arranque por esto
        db.session.rollback()
        app.logger.error("No se pudieron revisar las columnas: %s", exc)


def _ensure_url_columns(app: Flask) -> None:
    """Amplía las columnas de PostgreSQL para guardar URLs de imágenes."""
    from sqlalchemy import Text, inspect, text

    if db.engine.dialect.name != "postgresql":
        return

    columns = {
        "users": ("avatar",),
        "posts": ("attachment",),
    }
    inspector = inspect(db.engine)
    try:
        for table, names in columns.items():
            existing = {column["name"]: column["type"]
                        for column in inspector.get_columns(table)}
            for name in names:
                if name in existing and not isinstance(existing[name], Text):
                    db.session.execute(text(
                        f'ALTER TABLE "{table}" ALTER COLUMN "{name}" TYPE TEXT'
                    ))
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.error("No se pudieron ampliar las columnas de URL: %s", exc)


def _ensure_admin(app: Flask) -> None:
    """Crea la cuenta de administrador si todavía no hay ningún admin.

    Credenciales: ADMIN_USERNAME / ADMIN_EMAIL / ADMIN_PASSWORD (variables de entorno o .env).
    Inicia sesión con el CORREO + la contraseña. Si ya existe algún admin no toca nada
    (así no se pisa una contraseña que hayas cambiado)."""
    from .models import User

    cfg = app.config
    username, email, password = cfg.get("ADMIN_USERNAME"), cfg.get("ADMIN_EMAIL"), cfg.get("ADMIN_PASSWORD")
    if not (username and email and password):
        app.logger.info("Variables de ADMIN no configuradas. Omitiendo creación de admin.")
        return

    try:
        if User.query.filter_by(is_admin=True).first():
            return
        email = email.lower()
        # Reutiliza una cuenta existente con ese correo o nombre antes de crear otra.
        admin = (User.query.filter_by(email=email).first()
                 or User.query.filter(db.func.lower(User.username) == username.lower()).first())
        if admin is None:
            admin = User(username=username, email=email)
        admin.is_admin = True
        admin.is_banned = False
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        app.logger.warning("Cuenta admin creada: %s", email)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.error("Error al crear la cuenta admin: %s", exc)
