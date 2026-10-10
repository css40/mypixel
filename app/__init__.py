"""Application factory de MyPixel."""
import os
from pathlib import Path

from flask import Flask

from config import DEV_SECRET_KEY, config_by_name, detect_config_name

from .extensions import csrf, db, login_manager, socketio


def _asset_version(static_dir: Path) -> str:
    """Versión de los estáticos = fecha de modificación más reciente. Se añade como
    ?v=... a las URLs, así el navegador cachea sin riesgo de servir CSS/JS viejo."""
    try:
        newest = max(p.stat().st_mtime for p in static_dir.rglob("*") if p.is_file())
        return str(int(newest))
    except ValueError:
        return "1"


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or detect_config_name()

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    if config_name == "production" and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Define la variable de entorno SECRET_KEY antes de ejecutar en producción.")

    app.config["ASSET_VERSION"] = _asset_version(Path(app.static_folder))

    # Carpetas necesarias
    for sub in ("avatars", "attachments"):
        (app.config["UPLOAD_FOLDER"] / sub).mkdir(parents=True, exist_ok=True)

    # Extensiones
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    socketio.init_app(
        app,
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        ping_interval=app.config["SOCKETIO_PING_INTERVAL"],
        ping_timeout=app.config["SOCKETIO_PING_TIMEOUT"],
        cors_allowed_origins=None,          # solo mismo origen
        max_http_buffer_size=64 * 1024,     # un mensaje de chat nunca necesita más
        logger=False,
        engineio_logger=False,
    )

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

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        unread = current_user.unread_notifications_count if current_user.is_authenticated else 0
        return {
            "LANGUAGES": app.config["LANGUAGES"],
            "ASSET_VERSION": app.config["ASSET_VERSION"],
            "unread_notifications_count": unread,
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    with app.app_context():
        from .db_tools import init_database
        init_database(app)

    return app
