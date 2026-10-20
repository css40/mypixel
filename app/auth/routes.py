import re
import secrets

from flask import (current_app, flash, redirect, render_template, request, session,
                   url_for)
from flask_login import current_user, login_required, login_user, logout_user
from authlib.integrations.base_client.errors import OAuthError
from requests import RequestException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db
from ..models import User
from ..utils import is_safe_next
from . import bp
from .forms import LoginForm, RegisterForm


def _oauth_client(provider):
    oauth = current_app.extensions["authlib.integrations.flask_client"]
    return oauth.create_client(provider)


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
    callback_url = url_for("auth.authorize", provider=provider, _external=True)
    return client.authorize_redirect(callback_url)


@bp.route("/authorize/<provider>")
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
    except (OAuthError, RequestException, ValueError, TypeError) as exc:
        db.session.rollback()
        current_app.logger.warning("Falló la autenticación OAuth de %s: %s", provider, exc)
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
