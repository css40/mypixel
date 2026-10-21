from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..extensions import socketio
from ..models import (Group, GroupBan, GroupInvite, GroupMember, Message, Notification,
                      ReadMarker, User, dm_room)
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
    other = Group.query
    if my_group_ids:
        other = other.filter(~Group.id.in_(my_group_ids))
    if not current_user.is_admin:
        other = other.filter(Group.is_private.is_(False))   # los privados no se listan
    other_groups = other.order_by(Group.created_at.desc()).limit(20).all()

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
    members = sorted(grp.members, key=lambda m: (m.user_id != grp.owner_id, m.user.username.lower()))
    return render_template("chat/room.html", kind="group", target_id=grp.id, title=grp.name,
                           other=None, group=grp, messages=messages, members=members,
                           partners=partners, my_groups=my_groups, active_key=f"group_{grp.id}",
                           room=f"group_{grp.id}")


def _invite_users(grp, usernames):
    """Crea invitaciones (y su aviso en Notificaciones). Devuelve (invitados, no_encontrados)."""
    invited, missing = [], []
    for name in usernames:
        user = User.query.filter(func.lower(User.username) == name.lower()).first()
        if not user or user.is_banned:
            missing.append(name)
            continue
        if user.id == current_user.id or grp.has_member(user):
            continue
        if GroupInvite.query.filter_by(group_id=grp.id, user_id=user.id, status="pending").first():
            continue
        GroupBan.query.filter_by(group_id=grp.id, user_id=user.id).delete()  # volver a invitar = perdonar
        db.session.add(GroupInvite(group_id=grp.id, user_id=user.id, inviter_id=current_user.id))
        Notification.notify(user.id, current_user.id, "group_invite",
                            message=f"Te invitó al grupo privado «{grp.name}».")
        invited.append(user.username)
    return invited, missing


def _split_usernames(raw):
    seen, result = set(), []
    for chunk in (raw or "").replace("@", "").replace(";", ",").replace("\n", ",").split(","):
        name = chunk.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    return result[:20]


@bp.route("/groups/new", methods=["GET", "POST"])
@login_required
def new_group():
    form = GroupForm()
    if form.validate_on_submit():
        grp = Group(name=form.name.data.strip(), description=(form.description.data or "").strip(),
                    is_private=bool(form.is_private.data), owner_id=current_user.id)
        grp.members.append(GroupMember(user_id=current_user.id, role="admin"))
        db.session.add(grp)
        db.session.flush()
        invited, missing = ([], [])
        if grp.is_private:
            invited, missing = _invite_users(grp, _split_usernames(form.invite_users.data))
        if not _commit_chat_changes():
            partners, my_groups = _sidebar_data()
            return render_template("chat/new_group.html", form=form, partners=partners,
                                   my_groups=my_groups, active_key=None)
        flash("Grupo privado creado." if grp.is_private else "Grupo público creado.", "success")
        if invited:
            flash("Invitaciones enviadas a: " + ", ".join(invited) + ".", "info")
        if missing:
            flash("No encontré a: " + ", ".join(missing) + ".", "error")
        return redirect(url_for("chat.group", group_id=grp.id))
    partners, my_groups = _sidebar_data()
    return render_template("chat/new_group.html", form=form, partners=partners,
                           my_groups=my_groups, active_key=None)


def _can_manage(grp):
    return grp.owner_id == current_user.id or current_user.is_admin


@bp.route("/group/<int:group_id>/invite", methods=["POST"])
@login_required
def invite_to_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if not _can_manage(grp):
        abort(403)
    if not grp.is_private:
        flash("Los grupos públicos no necesitan invitación: cualquiera puede unirse.", "info")
        return redirect(url_for("chat.group", group_id=grp.id))
    names = _split_usernames(request.form.get("usernames", ""))
    if not names:
        flash("Escribe al menos un nombre de usuario.", "error")
        return redirect(url_for("chat.group", group_id=grp.id))
    invited, missing = _invite_users(grp, names)
    if not _commit_chat_changes():
        return redirect(url_for("chat.group", group_id=grp.id))
    if invited:
        flash("Invitación enviada a: " + ", ".join(invited) + ".", "success")
    if missing:
        flash("No encontré a: " + ", ".join(missing) + ".", "error")
    if not invited and not missing:
        flash("Esas personas ya están en el grupo o ya tienen una invitación pendiente.", "info")
    return redirect(url_for("chat.group", group_id=grp.id))


@bp.route("/group/<int:group_id>/kick/<int:user_id>", methods=["POST"])
@login_required
def kick_member(group_id, user_id):
    grp = db.get_or_404(Group, group_id)
    if not _can_manage(grp):
        abort(403)
    if user_id == grp.owner_id:
        flash("No se puede expulsar al creador del grupo.", "error")
        return redirect(url_for("chat.group", group_id=grp.id))
    member = GroupMember.query.filter_by(group_id=grp.id, user_id=user_id).first_or_404()
    username = member.user.username
    db.session.delete(member)
    if not GroupBan.query.filter_by(group_id=grp.id, user_id=user_id).first():
        db.session.add(GroupBan(group_id=grp.id, user_id=user_id))
    if not _commit_chat_changes():
        return redirect(url_for("chat.group", group_id=grp.id))
    # Si la persona tiene el chat abierto, se le saca al momento.
    socketio.emit("kicked", {"group_id": grp.id}, to=f"user_{user_id}")
    flash(f"{username} fue expulsado del grupo.", "info")
    return redirect(url_for("chat.group", group_id=grp.id))


@bp.route("/invite/<int:invite_id>/<action>", methods=["POST"])
@login_required
def answer_invite(invite_id, action):
    invite = db.get_or_404(GroupInvite, invite_id)
    if invite.user_id != current_user.id or action not in ("accept", "decline"):
        abort(403)
    if invite.status != "pending":
        flash("Esa invitación ya fue respondida.", "info")
        return redirect(url_for("main.notifications"))
    grp = invite.group
    if action == "accept":
        invite.status = "accepted"
        if not grp.has_member(current_user):
            db.session.add(GroupMember(group_id=grp.id, user_id=current_user.id))
        GroupBan.query.filter_by(group_id=grp.id, user_id=current_user.id).delete()
        if not _commit_chat_changes():
            return redirect(url_for("main.notifications"))
        flash(f"Te uniste a {grp.name}.", "success")
        return redirect(url_for("chat.group", group_id=grp.id))
    invite.status = "declined"
    if not _commit_chat_changes():
        return redirect(url_for("main.notifications"))
    flash("Invitación rechazada.", "info")
    return redirect(url_for("main.notifications"))


@bp.route("/group/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id):
    grp = db.get_or_404(Group, group_id)
    if grp.is_private and not current_user.is_admin:
        flash("Este grupo es privado: solo se entra con una invitación.", "error")
        return redirect(url_for("chat.index"))
    if GroupBan.query.filter_by(group_id=grp.id, user_id=current_user.id).first():
        flash("Fuiste expulsado de este grupo y no puedes volver a entrar.", "error")
        return redirect(url_for("chat.index"))
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
