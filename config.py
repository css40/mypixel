"""Configuración central de la aplicación."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    """URL de la base de datos.

    - Sin DATABASE_URL  -> SQLite local en instance/mypixel.db
    - Con DATABASE_URL  -> la que pongas (PostgreSQL de Render, por ejemplo).
    Render entrega "postgres://..." y SQLAlchemy necesita el driver explícito (psycopg 3).
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{BASE_DIR / 'instance' / 'mypixel.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    # La URL *externa* de Render (la que usas desde tu PC) exige SSL.
    if url.startswith("postgresql") and ".render.com" in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
    OAUTH_REDIRECT_BASE_URL = os.environ.get(
        "OAUTH_REDIRECT_BASE_URL", "https://mypixel-3po7.onrender.com"
    ).strip().rstrip("/")

    # Correo (recuperar contraseña). Se envía por la API HTTP de Brevo (gratis, funciona en Render,
    # donde los puertos SMTP están bloqueados). Sin BREVO_API_KEY el enlace solo se escribe en el log.
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "")          # correo verificado en Brevo
    MAIL_SENDER_NAME = os.environ.get("MAIL_SENDER_NAME", "MyPixel")
    PASSWORD_RESET_MAX_AGE = 3600                             # el enlace caduca en 1 hora

    # Base de datos (PostgreSQL en producción, SQLite en desarrollo local)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Render/PostgreSQL cierra conexiones inactivas: comprueba la conexión antes de usarla.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Subida de archivos
    UPLOAD_FOLDER = BASE_DIR / "uploads"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB por petición
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    ALLOWED_ATTACHMENT_EXTENSIONS = {
        "png", "jpg", "jpeg", "gif", "webp",
        "txt", "md", "pdf", "zip", "json", "csv",
        "py", "js", "ts", "java", "sql", "html", "css", "sh",
    }

    # Cuenta de administrador (se crea sola la primera vez si no existe ningún admin).
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@mypixel.com")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin#MyPixel2026")

    # Avatares retro (los mismos de RetroPixel): emoji + color de fondo
    AVATAR_EMOJIS = ["👾", "🦄", "🐸", "🤖", "👻", "🐙", "🌵", "🍄", "🚀", "🎮", "💾", "🕹️"]
    AVATAR_COLORS = ["#a35a5a", "#5f8f8b", "#c9a24a", "#7d6b8f", "#7f9a73", "#b87a4b"]

    # Valoraciones: un post es "recomendado" con >= N votos y media >= X.
    FEATURED_MIN_RATINGS = 3
    FEATURED_MIN_AVG = 4.0
    # Ranking "Mejor valorados": media bayesiana
    RATING_PRIOR_VOTES = 3
    RATING_PRIOR_MEAN = 3.0

    # Paginación
    POSTS_PER_PAGE = 10

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


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}