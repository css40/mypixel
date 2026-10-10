from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..chat.events import end_user_sessions
from ..extensions import db
from ..models import User
from ..utils import is_safe_next
from . import bp
from .forms import LoginForm, RegisterForm


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


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    user_id = current_user.id
    logout_user()
    end_user_sessions(user_id)   # los sockets de chat abiertos se cierran y vuelven al login
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))
