from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Group, GroupMember, Message, Notification, ReadMarker, User, dm_room
from ..utils import escape_like
from . import bp
from .forms import GroupForm

HISTORY_LIMIT = 50


@bp.errorhandler(SQLAlchemyError)
def handle_chat_database_error(_exc):
    db.session.rollback()
    current_app.logger.error(
        "Error de base de datos al procesar una ruta de chat",
        exc_info=(_exc.__class__, _exc, _exc.__traceback__),
    )
    return "El chat no está disponible temporalmente. Inténtalo de nuevo.", 503


def _commit_chat_changes() -> bool:
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error de base de datos en una operación de chat")
        flash("No se pudo completar la operación. Inténtalo de nuevo.", "error")
        return False
    return True


def _mark_read(user_id: int, conv_key: str) -> bool:
    try:
        ReadMarker.mark_read(user_id, conv_key)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Error al actualizar el marcador de lectura del chat")
        flash("No se pudo actualizar el estado de lectura.", "error")
        return False
    return True


def _read_markers(user_id):
    """dict conv_key -> last_read_at, de los marcadores persistentes del usuario."""
    return {m.conv_key: m.last_read_at for m in ReadMarker.query.filter_by(user_id=user_id)}


def _sidebar_data(extra_partner=None):
    """Conversaciones directas recientes + grupos del usuario, con marca de no-leído
    persistente (sobrevive a recargar la página o volver a entrar más tarde)."""
    from datetime import datetime
    epoch = datetime(1970, 1, 1)
    markers = _read_markers(current_user.id)

    partner_ids = []
    recent = (Message.query
              .filter(Message.group_id.is_(None),
                      or_(Message.sender_id == current_user.id,
                          Message.recipient_id == current_user.id))
              .order_by(Message.created_at.desc()).limit(300))
    for m in recent:
        pid = m.recipient_id if m.sender_id == current_user.id else m.sender_id
        if pid not in partner_ids:
            partner_ids.append(pid)
    if extra_partner and extra_partner.id not in partner_ids:
        partner_ids.insert(0, extra_partner.id)

    users = {u.id: u for u in User.query.filter(User.id.in_(partner_ids))} if partner_ids else {}
    partners = [users[i] for i in partner_ids if i in users]
    for u in partners:
        last_from_them = (db.session.query(func.max(Message.created_at))
                          .filter(Message.sender_id == u.id, Message.recipient_id == current_user.id)
                          .scalar())
        u.has_unread = bool(last_from_them and last_from_them > markers.get(f"dm_{u.id}", epoch))

    groups = (Group.query.join(GroupMember)
              .filter(GroupMember.user_id == current_user.id)
              .order_by(Group.name).all())
    for g in groups:
        last_from_others = (db.session.query(func.max(Message.created_at))
                            .filter(Message.group_id == g.id, Message.sender_id != current_user.id)
                            .scalar())
        g.has_unread = bool(last_from_others and last_from_others > markers.get(f"group_{g.id}", epoch))
    return partners, groups


@bp.route("/")
@login_required
def index():
    """Pantalla de inicio del chat: buscar personas y descubrir grupos."""
    q = request.args.get("q", "").strip()
    partners, my_groups = _sidebar_data()

    users = []
    if q:
        term = f"%{escape_like(q)}%"
        users = (User.query.filter(User.id != current_user.id,
                                   User.username.ilike(term, escape="\\"))
                 .order_by(User.username).limit(20).all())

    my_group_ids = {g.id for g in my_groups}
    other_groups = (Group.query.filter(~Group.id.in_(my_group_ids)) if my_group_ids
                    else Group.query).order_by(Group.created_at.desc()).limit(20).all()

    return render_template("chat/index.html", partners=partners, my_groups=my_groups,
                           found_users=users, other_groups=other_groups, q=q,
                           active_key=None)


@bp.route("/dm/<username>")
@login_required
def direct(username):
    other = User.query.filter(func.lower(User.username) == username.lower()).first_or_404()
    if other.id == current_user.id:
        flash("No puedes chatear contigo mismo.", "info")
        return redirect(url_for("chat.index"))

    messages = (Message.query
                .filter(Message.group_id.is_(None),
                        or_((Message.sender_id == current_user.id) & (Message.recipient_id == other.id),
                            (Message.sender_id == other.id) & (Message.recipient_id == current_user.id)))
                .order_by(Message.created_at.desc()).limit(HISTORY_LIMIT).all())
    messages.reverse()

    # Al abrir el chat, los avisos de "te escribió X" de la campana pasan a leídos.
    Notification.mark_dm_read(current_user.id, other.id)
    if not _mark_read(current_user.id, f"dm_{other.id}"):
        return redirect(url_for("chat.index"))
    partners, my_groups = _sidebar_data(extra_partner=other)
    return render_template("chat/room.html", kind="dm", target_id=other.id, title=f"@{other.username}",
                           other=other, group=None, messages=messages,
                           partners=partners, my_groups=my_groups, active_key=f"dm_{other.id}",
                           room=dm_room(current_user.id, other.id))


@bp.route("/group/<int:group_id>")
@login_required
def group(group_id):
    grp = db.get_or_404(Group, group_id)
    # El admin puede entrar a cualquier grupo para moderarlo sin tener que unirse.
    if not grp.has_member(current_user) and not current_user.is_admin:
        flash("Únete al grupo para ver la conversación.", "info")
        return redirect(url_for("chat.index"))

    messages = (grp.messages.order_by(None).order_by(Message.created_at.desc())
                .limit(HISTORY_LIMIT).all())
    messages.reverse()

    if not _mark_read(current_user.id, f"group_{grp.id}"):
        return redirect(url_for("chat.index"))
    partners, my_groups = _sidebar_data()
    return render_template("chat/room.html", kind="group", target_id=grp.id, title=grp.name,
                           other=None, group=grp, messages=messages,
                           partners=partners, my_groups=my_groups, active_key=f"group_{grp.id}",
                           room=f"group_{grp.id}")


@bp.route("/groups/new", methods=["GET", "POST"])
@login_required
def new_group():
    form = GroupForm()
    if form.validate_on_submit():
        grp = Group(name=form.name.data.strip(), description=(form.description.data or "").strip(),
                    owner_id=current_user.id)
        grp.members.append(GroupMember(user_id=current_user.id, role="admin"))
        db.session.add(grp)
        if not _commit_chat_changes():
            partners, my_groups = _sidebar_data()
            return render_template("chat/new_group.html", form=form, partners=partners,
                                   my_groups=my_groups, active_key=None)
        flash("Grupo creado.", "success")
        return redirect(url_for("chat.group", group_id=grp.id))
    partners, my_groups = _sidebar_data()
    return render_template("chat/new_group.html", form=form, partners=partners,
                           my_groups=my_groups, active_key=None)


@bp.route("/group/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if not grp.has_member(current_user):
        db.session.add(GroupMember(group_id=grp.id, user_id=current_user.id))
        if not _commit_chat_changes():
            return redirect(url_for("chat.group", group_id=grp.id))
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
    if not _commit_chat_changes():
        return redirect(url_for("chat.group", group_id=grp.id))
    flash(f"Saliste de {grp.name}.", "info")
    return redirect(url_for("chat.index"))


@bp.route("/group/<int:group_id>/delete", methods=["POST"])
@login_required
def delete_group(group_id):
    grp = db.get_or_404(Group, group_id)
    # Lo borra su creador o cualquier administrador de la web.
    if grp.owner_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(grp)
    if not _commit_chat_changes():
        return redirect(url_for("chat.index"))
    flash("Grupo eliminado.", "info")
    return redirect(url_for("chat.index"))
