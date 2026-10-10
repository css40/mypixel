"""Configuración central de MyPixel."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEV_SECRET_KEY = "dev-only-change-me"


def _database_url() -> str:
    url = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'mypixel.db'}"
    )
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def _engine_options(url: str) -> dict:
    """Opciones del pool de conexiones.

    Con una base de datos remota (PostgreSQL) las conexiones inactivas pueden ser
    cortadas por el servidor o por un balanceador. `pool_pre_ping` comprueba la
    conexión antes de usarla y `pool_recycle` la renueva antes de que caduque, lo
    que evita los típicos errores "SSL connection has been closed unexpectedly".
    """
    if url.startswith("sqlite"):
        return {"pool_pre_ping": True}
    return {
        "pool_pre_ping": True,
        "pool_recycle": 240,
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 5)),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 5)),
        "pool_timeout": 20,
        "connect_args": {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    }


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)

    # Base de datos (PostgreSQL en producción, SQLite en desarrollo local)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_CREATE_DB = os.environ.get("AUTO_CREATE_DB", "1") != "0"

    # Socket.IO (chat en tiempo real)
    SOCKETIO_ASYNC_MODE = os.environ.get("SOCKETIO_ASYNC_MODE") or "threading"
    SOCKETIO_PING_INTERVAL = 20   # segundos entre pings del servidor
    SOCKETIO_PING_TIMEOUT = 40    # tiempo máximo sin respuesta antes de cerrar

    # Archivos estáticos: las URLs llevan ?v=<versión>, así que se pueden cachear.
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(days=30)

    # Subida de archivos
    UPLOAD_FOLDER = BASE_DIR / "uploads"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB por petición
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    ALLOWED_ATTACHMENT_EXTENSIONS = {
        "png", "jpg", "jpeg", "gif", "webp",
        "txt", "md", "pdf", "zip", "json", "csv",
        "py", "js", "ts", "java", "sql", "html", "css", "sh",
    }

    # Cuenta de administrador: se crea sola la primera vez SOLO si defines
    # ADMIN_PASSWORD en el entorno (ya no existe una contraseña por defecto).
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@mypixel.com")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

    # Valoraciones: un post es "recomendado" con >= N votos y media >= X.
    FEATURED_MIN_RATINGS = 3
    FEATURED_MIN_AVG = 4.0
    # Ranking "Mejor valorados": media bayesiana
    RATING_PRIOR_VOTES = 3
    RATING_PRIOR_MEAN = 3.0

    # Paginación y cachés pequeños
    POSTS_PER_PAGE = 10
    SIDEBAR_CACHE_SECONDS = 30

    # Lenguajes / categorías disponibles: (etiqueta, alias de highlight.js)
    LANGUAGES = [
        ("Python", "python"),
        ("JavaScript", "javascript"),
        ("TypeScript", "typescript"),
        ("Java", "java"),
        ("C#", "csharp"),
        ("C++", "cpp"),
        ("Go", "go"),
        ("Rust", "rust"),
        ("PHP", "php"),
        ("SQL", "sql"),
        ("HTML/CSS", "xml"),
        ("Bash", "bash"),
        ("Otro", "plaintext"),
    ]


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    AUTO_CREATE_DB = True


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def detect_config_name() -> str:
    """APP_ENV / FLASK_ENV manda; si no hay, una base PostgreSQL implica producción."""
    explicit = os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV")
    if explicit in config_by_name:
        return explicit
    return "development" if Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite") else "production"
