"""Rutas HTTP del chat (controladores finos: la lógica vive en services/chat_service.py)."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Group, GroupMember, Message, User, dm_room
from ..services import chat_service as chat
from ..utils import escape_like
from . import bp
from .forms import GroupForm


def _commit(error_message="No se pudo completar la operación. Inténtalo de nuevo.") -> bool:
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos en una operación de chat")
        flash(error_message, "error")
        return False
    return True


def _render(template, **ctx):
    """Renderiza una página del chat añadiendo siempre los datos de la barra lateral."""
    partners, my_groups = chat.sidebar_data(current_user, extra_partner=ctx.pop("extra_partner", None))
    return render_template(template, partners=partners, my_groups=my_groups, **ctx)


@bp.route("/")
@login_required
def index():
    """Pantalla de inicio del chat: buscar personas y descubrir grupos."""
    q = request.args.get("q", "").strip()
    users = []
    if q:
        term = f"%{escape_like(q)}%"
        users = db.session.execute(
            select(User).where(User.id != current_user.id, User.is_banned.is_(False),
                               User.username.ilike(term, escape="\\"))
            .order_by(User.username).limit(20)).scalars().all()

    partners, my_groups = chat.sidebar_data(current_user)
    other_groups = chat.discoverable_groups({g.id for g in my_groups})
    return render_template("chat/index.html", partners=partners, my_groups=my_groups,
                           found_users=users, other_groups=other_groups, q=q, active_key=None)


@bp.route("/dm/<username>")
@login_required
def direct(username):
    other = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    if other.id == current_user.id:
        flash("No puedes chatear contigo mismo.", "info")
        return redirect(url_for("chat.index"))

    messages = chat.dm_history(current_user.id, other.id)
    chat.mark_read(current_user.id, f"dm_{other.id}")
    return _render("chat/room.html", extra_partner=other, kind="dm", target_id=other.id,
                   title=f"@{other.username}", other=other, group=None, messages=messages,
                   active_key=f"dm_{other.id}", room=dm_room(current_user.id, other.id))


@bp.route("/group/<int:group_id>")
@login_required
def group(group_id):
    grp = db.get_or_404(Group, group_id)
    if not grp.has_member(current_user):
        flash("Únete al grupo para ver la conversación.", "info")
        return redirect(url_for("chat.index"))

    grp.member_total = chat.member_count(grp.id)
    messages = chat.group_history(grp.id)
    chat.mark_read(current_user.id, f"group_{grp.id}")
    return _render("chat/room.html", kind="group", target_id=grp.id, title=grp.name,
                   other=None, group=grp, messages=messages,
                   active_key=f"group_{grp.id}", room=f"group_{grp.id}")


@bp.route("/groups/new", methods=["GET", "POST"])
@login_required
def new_group():
    form = GroupForm()
    if form.validate_on_submit():
        grp = Group(name=form.name.data.strip(), description=(form.description.data or "").strip(),
                    owner_id=current_user.id)
        grp.members.append(GroupMember(user_id=current_user.id, role="admin"))
        db.session.add(grp)
        if _commit():
            flash("Grupo creado.", "success")
            return redirect(url_for("chat.group", group_id=grp.id))
    return _render("chat/new_group.html", form=form, active_key=None)


@bp.route("/group/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if not grp.has_member(current_user):
        db.session.add(GroupMember(group_id=grp.id, user_id=current_user.id))
        if _commit():
            flash(f"Te uniste a {grp.name}.", "success")
    return redirect(url_for("chat.group", group_id=grp.id))


@bp.route("/group/<int:group_id>/leave", methods=["POST"])
@login_required
def leave_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if grp.owner_id == current_user.id:
        flash("El creador no puede salir; elimina el grupo si ya no lo necesitas.", "error")
        return redirect(url_for("chat.group", group_id=grp.id))
    GroupMember.query.filter_by(group_id=grp.id, user_id=current_user.id).delete()
    if not _commit():
        return redirect(url_for("chat.group", group_id=grp.id))
    flash(f"Saliste de {grp.name}.", "info")
    return redirect(url_for("chat.index"))


@bp.route("/group/<int:group_id>/delete", methods=["POST"])
@login_required
def delete_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if grp.owner_id != current_user.id:
        abort(403)
    # Borrado masivo: evita cargar en memoria (y borrar uno a uno) todos los mensajes.
    Message.query.filter_by(group_id=grp.id).delete(synchronize_session=False)
    GroupMember.query.filter_by(group_id=grp.id).delete(synchronize_session=False)
    db.session.delete(grp)
    if _commit():
        flash("Grupo eliminado.", "info")
    return redirect(url_for("chat.index"))
