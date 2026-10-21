import re
import secrets
from urllib.parse import urlsplit, urlunsplit

from flask import (current_app, flash, redirect, render_template, request, session,
                   url_for)
from flask_login import current_user, login_required, login_user, logout_user
from authlib.integrations.base_client.errors import OAuthError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from requests import RequestException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db
from ..models import User
from ..utils import is_safe_next, send_email
from . import bp
from .forms import (ChangePasswordForm, ForgotPasswordForm, LoginForm, RegisterForm,
                    ResetPasswordForm)


def _oauth_client(provider):
    oauth = current_app.extensions["authlib.integrations.flask_client"]
    return oauth.create_client(provider)


def _oauth_callback_url(provider):
    base_url = current_app.config["OAUTH_REDIRECT_BASE_URL"].strip()
    parsed = urlsplit(base_url)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OAUTH_REDIRECT_BASE_URL debe contener un host público válido.")
    return urlunsplit(("https", parsed.netloc, f"/authorize/{provider}", "", ""))


def _log_oauth_error(provider, phase, exc):
    current_app.logger.error(
        "Error OAuth %s durante %s: tipo=%s codigo=%s descripcion=%s uri=%s",
        provider,
        phase,
        type(exc).__name__,
        getattr(exc, "error", None),
        getattr(exc, "description", None) or str(exc),
        getattr(exc, "uri", None),
    )


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))
    form = RegisterForm()
    try:
        if form.validate_on_submit():
            user = User(username=form.username.data, email=form.email.data.lower())
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Cuenta creada. ¡Bienvenido/a!", "success")
            return redirect(url_for("profile.edit"))
    except IntegrityError:
        db.session.rollback()
        flash("Ya existe una cuenta con ese nombre o correo.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos durante el registro")
        flash("No se pudo crear la cuenta por un error de base de datos. Inténtalo de nuevo.", "error")
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            if user.is_banned:
                flash("Esta cuenta está suspendida.", "error")
                return render_template("auth/login.html", form=form)
            login_user(user, remember=form.remember.data)
            nxt = request.args.get("next")
            return redirect(nxt if is_safe_next(nxt) else url_for("posts.feed"))
        flash("Correo o contraseña incorrectos.", "error")
    return render_template("auth/login.html", form=form)


# --------------------------------------------------------------------------- #
# Recuperar y cambiar contraseña
# --------------------------------------------------------------------------- #
_RESET_SALT = "password-reset"


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_RESET_SALT)


def _fingerprint(user) -> str:
    """Huella del hash actual: al cambiar la contraseña el enlace anterior deja de valer (un solo uso)."""
    return user.password_hash[-16:]


def _reset_token(user) -> str:
    return _serializer().dumps({"uid": user.id, "fp": _fingerprint(user)})


def _user_from_token(token):
    try:
        data = _serializer().loads(token, max_age=current_app.config["PASSWORD_RESET_MAX_AGE"])
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid")) if isinstance(data, dict) else None
    if user is None or data.get("fp") != _fingerprint(user):
        return None
    return user


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("auth.change_password"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user:
            base = (request.host_url.rstrip("/") if current_app.config.get("DEBUG")
                    else current_app.config["OAUTH_REDIRECT_BASE_URL"])
            link = base + url_for("auth.reset_password", token=_reset_token(user))
            minutes = current_app.config["PASSWORD_RESET_MAX_AGE"] // 60
            send_email(
                user.email, "Restablece tu contraseña de MyPixel",
                html=(f"<p>Hola <b>{user.username}</b>,</p>"
                      f"<p>Recibimos una solicitud para restablecer tu contraseña. "
                      f'<a href="{link}">Haz clic aquí para elegir una nueva</a> '
                      f"(el enlace caduca en {minutes} minutos y solo sirve una vez).</p>"
                      f"<p>Si no fuiste tú, ignora este correo: tu contraseña sigue igual.</p>"),
                text=f"Restablece tu contraseña (caduca en {minutes} min): {link}")
        # Mismo mensaje exista o no la cuenta, para no revelar qué correos están registrados.
        flash("Si ese correo tiene una cuenta, se procesará la solicitud para restablecer la contraseña. "
              "Revisa también la carpeta de spam.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = _user_from_token(token)
    if user is None:
        flash("El enlace no es válido o ya caducó. Pide uno nuevo.", "error")
        return redirect(url_for("auth.forgot_password"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            user.set_password(form.password.data)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Error al restablecer la contraseña")
            flash("No se pudo guardar la contraseña. Inténtalo de nuevo.", "error")
            return render_template("auth/reset.html", form=form)
        flash("Contraseña actualizada. Ya puedes iniciar sesión.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", form=form)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current.data):
            form.current.errors.append("La contraseña actual no es correcta.")
        elif form.current.data == form.password.data:
            form.password.errors.append("La nueva contraseña debe ser distinta de la actual.")
        else:
            try:
                current_user.set_password(form.password.data)
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception("Error al cambiar la contraseña")
                flash("No se pudo cambiar la contraseña. Inténtalo de nuevo.", "error")
                return render_template("auth/change_password.html", form=form)
            flash("Contraseña cambiada correctamente.", "success")
            return redirect(url_for("profile.edit"))
    return render_template("auth/change_password.html", form=form)


@bp.route("/login/<provider>")
def social_login(provider):
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))
    if provider not in {"google", "github"}:
        return redirect(url_for("auth.login"))
    client = _oauth_client(provider)
    if client is None:
        current_app.logger.error("OAuth de %s solicitado sin credenciales configuradas.", provider)
        flash(f"El inicio de sesión con {provider.title()} no está configurado.", "error")
        return redirect(url_for("auth.login"))

    next_url = request.args.get("next", "")
    session[f"oauth_next_{provider}"] = next_url if is_safe_next(next_url) else ""
    try:
        callback_url = _oauth_callback_url(provider)
        current_app.logger.info(
            "Iniciando OAuth %s con redirect_uri=%s", provider, callback_url
        )
        return client.authorize_redirect(callback_url)
    except OAuthError as exc:
        _log_oauth_error(provider, "solicitud de autorización", exc)
    except RequestException:
        current_app.logger.exception("Error de red al iniciar OAuth %s", provider)
    except ValueError:
        current_app.logger.exception("URL de callback inválida para OAuth %s", provider)
    flash("No se pudo iniciar la autenticación con el proveedor. Inténtalo de nuevo.", "error")
    return redirect(url_for("auth.login"))


def authorize(provider):
    if provider not in {"google", "github"}:
        return redirect(url_for("auth.login"))
    client = _oauth_client(provider)
    if client is None:
        flash(f"El inicio de sesión con {provider.title()} no está configurado.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = client.authorize_access_token()
        if provider == "google":
            profile = token.get("userinfo") or {}
            if not isinstance(profile, dict):
                raise ValueError("Google devolvió un perfil no válido.")
            if profile.get("email_verified") is not True:
                raise ValueError("Google no confirmó que el correo esté verificado.")
            email = profile.get("email")
            username = email.partition("@")[0] if isinstance(email, str) else ""
        else:
            user_response = client.get("user")
            user_response.raise_for_status()
            profile = user_response.json()
            emails_response = client.get("user/emails")
            emails_response.raise_for_status()
            emails = emails_response.json()
            if not isinstance(profile, dict) or not isinstance(emails, list):
                raise ValueError("GitHub devolvió datos de cuenta no válidos.")
            primary_email = next(
                (entry.get("email") for entry in emails
                 if isinstance(entry, dict) and entry.get("primary") and entry.get("verified")),
                None,
            )
            email = primary_email or next(
                (entry.get("email") for entry in emails if isinstance(entry, dict)
                 and entry.get("verified")),
                None,
            )
            username = profile.get("login", "")

        if not isinstance(email, str) or not email.strip():
            raise ValueError("El proveedor no proporcionó un correo verificado.")
        email = email.strip().lower()
        if len(email) > 120 or "@" not in email:
            raise ValueError("El proveedor devolvió un correo no válido.")

        user = User.query.filter(func.lower(User.email) == email).first()
        if user is None:
            user = User(
                username=_unique_oauth_username(username or email.partition("@")[0]),
                email=email,
                is_verified=True,
            )
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                user = User.query.filter(func.lower(User.email) == email).first()
                if user is None:
                    raise

        if user.is_banned:
            flash("Esta cuenta está suspendida.", "error")
            return redirect(url_for("auth.login"))
        login_user(user)
        next_url = session.pop(f"oauth_next_{provider}", "")
        return redirect(next_url if is_safe_next(next_url) else url_for("posts.feed"))
    except OAuthError as exc:
        db.session.rollback()
        _log_oauth_error(provider, "intercambio de código/verificación", exc)
        flash("No se pudo verificar tu cuenta con el proveedor. Inténtalo de nuevo.", "error")
    except RequestException:
        db.session.rollback()
        current_app.logger.exception(
            "Error HTTP durante el intercambio de código/verificación OAuth %s",
            provider,
        )
        flash("No se pudo verificar tu cuenta con el proveedor. Inténtalo de nuevo.", "error")
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        current_app.logger.warning(
            "Datos de autenticación OAuth no válidos para %s: %s", provider, exc
        )
        flash("No se pudo verificar tu cuenta con el proveedor. Inténtalo de nuevo.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos durante OAuth de %s", provider)
        flash("No se pudo iniciar sesión por un error de base de datos.", "error")
    return redirect(url_for("auth.login"))


def _unique_oauth_username(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")[:30] or "player"
    candidate = base
    while User.query.filter(func.lower(User.username) == candidate.lower()).first():
        suffix = secrets.token_hex(3)
        candidate = f"{base[:23]}_{suffix}"
    return candidate


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))
