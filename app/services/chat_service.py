"""Lógica del chat: salas, historial, barra lateral y envío de mensajes.

Las rutas (`chat/routes.py`) y los eventos Socket.IO (`chat/events.py`) son capas
finas que validan la petición y delegan aquí. Todas las consultas están pensadas
para ejecutarse en un número constante de queries (sin N+1), porque la base de
datos está en un servidor remoto y cada ida y vuelta cuesta.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from flask import current_app
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Group, GroupMember, Message, ReadMarker, User, dm_room

HISTORY_LIMIT = 50
MISSED_LIMIT = 100
MAX_LEN = 2000
EPOCH = datetime(1970, 1, 1)

# ```python\n...código...\n```  (o solo ```  sin lenguaje). Si el mensaje entero es un
# único bloque así, se guarda y se muestra como código, no como burbuja de texto.
_FENCE_RE = re.compile(r"^```([\w+#.\-]*)\s*\n?(.*?)\n?```$", re.DOTALL)
_LANG_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python", "rb": "ruby",
    "sh": "bash", "shell": "bash", "yml": "yaml", "cs": "csharp", "c++": "cpp",
}


class ChatError(Exception):
    """Error que se puede mostrar tal cual al usuario."""


@dataclass
class SentMessage:
    payload: dict
    room: str
    notify_ids: list = field(default_factory=list)
    notify_key: str = ""
    preview: str = ""


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def conv_key(kind: str, target_id: int) -> str:
    return f"dm_{target_id}" if kind == "dm" else f"group_{target_id}"


def extract_code(body: str):
    """Si `body` es un único bloque ```lang ... ```, devuelve (codigo, lenguaje);
    si no, (None, None) y el mensaje se trata como texto normal."""
    match = _FENCE_RE.match(body.strip())
    if not match:
        return None, None
    lang = (match.group(1) or "plaintext").strip().lower()
    code = match.group(2)
    if not code.strip():
        return None, None
    return code, _LANG_ALIASES.get(lang, lang)


def resolve_target(kind, target_id, user):
    """(sala, destino) si `user` puede usar la conversación; si no, (None, None)."""
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return None, None

    if kind == "dm":
        other = db.session.get(User, target_id)
        if other and other.id != user.id:
            return dm_room(user.id, other.id), other
    elif kind == "group":
        grp = db.session.get(Group, target_id)
        if grp and grp.has_member(user):
            return f"group_{grp.id}", grp
    return None, None


def mark_read(user_id: int, key: str) -> bool:
    """Marca una conversación como leída. Es secundario: si falla, se registra y se sigue."""
    try:
        ReadMarker.mark_read(user_id, key)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning("No se pudo actualizar el marcador de lectura", exc_info=True)
        return False
    return True


# --------------------------------------------------------------------------- #
# Historial
# --------------------------------------------------------------------------- #
def _dm_filter(user_id, other_id):
    return and_(
        Message.group_id.is_(None),
        or_(and_(Message.sender_id == user_id, Message.recipient_id == other_id),
            and_(Message.sender_id == other_id, Message.recipient_id == user_id)),
    )


def dm_history(user_id, other_id, limit=HISTORY_LIMIT):
    stmt = (select(Message).options(joinedload(Message.sender))
            .where(_dm_filter(user_id, other_id))
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit))
    rows = db.session.execute(stmt).scalars().all()
    rows.reverse()
    return rows


def group_history(group_id, limit=HISTORY_LIMIT):
    stmt = (select(Message).options(joinedload(Message.sender))
            .where(Message.group_id == group_id)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit))
    rows = db.session.execute(stmt).scalars().all()
    rows.reverse()
    return rows


def messages_after(user_id, kind, target_id, after_id, limit=MISSED_LIMIT):
    """Mensajes posteriores a `after_id` (para recuperar lo perdido tras una reconexión).
    El acceso a la conversación ya debe haberse validado con `resolve_target`."""
    cond = _dm_filter(user_id, target_id) if kind == "dm" else (Message.group_id == target_id)
    stmt = (select(Message).options(joinedload(Message.sender))
            .where(cond, Message.id > after_id).order_by(Message.id.asc()).limit(limit))
    return [m.to_dict() for m in db.session.execute(stmt).scalars()]


# --------------------------------------------------------------------------- #
# Barra lateral (conversaciones recientes + grupos, con "no leído" persistente)
# --------------------------------------------------------------------------- #
def sidebar_data(user, extra_partner=None, limit=40):
    """Devuelve (contactos, grupos) con `has_unread` calculado en un nº fijo de
    consultas, sin importar cuántas conversaciones tenga el usuario."""
    markers = {m.conv_key: m.last_read_at
               for m in db.session.execute(
                   select(ReadMarker).where(ReadMarker.user_id == user.id)).scalars()}

    # Última actividad por contacto: dos agrupaciones sencillas (usan los índices
    # de messages) en vez de cargar cientos de mensajes y consultar uno por uno.
    sent = dict(db.session.execute(
        select(Message.recipient_id, func.max(Message.created_at))
        .where(Message.sender_id == user.id, Message.recipient_id.is_not(None))
        .group_by(Message.recipient_id)).all())
    received = dict(db.session.execute(
        select(Message.sender_id, func.max(Message.created_at))
        .where(Message.recipient_id == user.id)
        .group_by(Message.sender_id)).all())

    activity = {pid: max(sent.get(pid, EPOCH), received.get(pid, EPOCH))
                for pid in set(sent) | set(received)}
    partner_ids = sorted(activity, key=activity.get, reverse=True)[:limit]
    if extra_partner and extra_partner.id not in partner_ids:
        partner_ids.insert(0, extra_partner.id)

    partners = []
    if partner_ids:
        users = {u.id: u for u in db.session.execute(
            select(User).where(User.id.in_(partner_ids))).scalars()}
        for pid in partner_ids:
            u = users.get(pid)
            if not u:
                continue
            last_from_them = received.get(pid)
            u.has_unread = bool(last_from_them and last_from_them > markers.get(f"dm_{pid}", EPOCH))
            partners.append(u)

    groups = db.session.execute(
        select(Group).join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user.id).order_by(Group.name)).scalars().all()
    if groups:
        last_by_group = dict(db.session.execute(
            select(Message.group_id, func.max(Message.created_at))
            .where(Message.group_id.in_([g.id for g in groups]), Message.sender_id != user.id)
            .group_by(Message.group_id)).all())
        for g in groups:
            last = last_by_group.get(g.id)
            g.has_unread = bool(last and last > markers.get(f"group_{g.id}", EPOCH))
    return partners, groups


def discoverable_groups(exclude_ids, limit=20):
    """Grupos a los que el usuario aún no pertenece, con su nº de miembros."""
    stmt = (select(Group, func.count(GroupMember.id))
            .outerjoin(GroupMember, GroupMember.group_id == Group.id)
            .group_by(Group.id).order_by(Group.created_at.desc()).limit(limit))
    if exclude_ids:
        stmt = stmt.where(Group.id.not_in(exclude_ids))
    groups = []
    for grp, total in db.session.execute(stmt).all():
        grp.member_total = total
        groups.append(grp)
    return groups


def member_count(group_id) -> int:
    return db.session.query(func.count(GroupMember.id)).filter_by(group_id=group_id).scalar() or 0


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
def create_message(sender, kind, target_id, body) -> SentMessage:
    """Valida, guarda el mensaje y devuelve lo necesario para difundirlo.

    El mensaje se confirma (commit) ANTES de actualizar el marcador de lectura, así
    un fallo secundario nunca hace perder el mensaje."""
    body = (body or "").strip() if isinstance(body, str) else ""
    if not body or len(body) > MAX_LEN:
        raise ChatError(f"Mensaje vacío o demasiado largo (máx. {MAX_LEN}).")
    if sender.is_banned:
        raise ChatError("Tu sesión ya no está activa. Inicia sesión otra vez.")

    room, target = resolve_target(kind, target_id, sender)
    if not room or (kind == "dm" and target.is_banned):
        raise ChatError("No puedes escribir en esta conversación.")

    code, lang = extract_code(body)
    is_code = code is not None
    stored = code if is_code else body
    fields = dict(sender=sender, body=stored, is_code=is_code, code_language=lang)
    if kind == "dm":
        msg = Message(recipient_id=target.id, **fields)
        notify_ids = [target.id]
    else:
        msg = Message(group_id=target.id, **fields)
        notify_ids = list(db.session.execute(
            select(GroupMember.user_id)
            .where(GroupMember.group_id == target.id, GroupMember.user_id != sender.id)).scalars())

    # Se capturan antes del commit: después de confirmar, SQLAlchemy "expira" los
    # objetos y volver a leerlos costaría una consulta extra por cada atributo.
    sender_id, target_pk = sender.id, target.id

    db.session.add(msg)
    db.session.flush()              # asigna id y created_at sin cerrar la transacción
    payload = msg.to_dict()
    payload["room"] = room
    preview = ("[código] " + stored[:60]) if is_code else stored[:80]
    db.session.commit()

    mark_read(sender_id, conv_key(kind, target_pk))
    return SentMessage(payload=payload, room=room, notify_ids=notify_ids,
                       notify_key=f"dm_{sender_id}" if kind == "dm" else f"group_{target_pk}",
                       preview=preview)
