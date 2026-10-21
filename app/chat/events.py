"""Eventos de Flask-SocketIO.

Salas:
  user_<id>      -> sala personal (notificaciones)
  dm_<a>_<b>     -> chat directo entre dos usuarios (a < b)
  group_<id>     -> chat de grupo

El servidor SIEMPRE valida permisos: un cliente no puede unirse ni escribir en
una sala que no le corresponde aunque manipule los eventos desde el navegador.
"""
import re

from flask import current_app
from flask_login import current_user
from flask_socketio import emit, join_room, leave_room
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db, socketio
from ..models import Group, Message, ReadMarker, User, dm_room

MAX_LEN = 2000

# ```python\n...código...\n```  (o solo ```  sin lenguaje). Si el mensaje entero
# es un único bloque así, se guarda y se muestra como código, no como burbuja de texto.
_FENCE_RE = re.compile(r"^```([\w+#.\-]*)\s*\n?(.*?)\n?```$", re.DOTALL)

# Alias de lenguaje -> clase de highlight.js, tomado de la configuración de la app
_HLJS_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python", "rb": "ruby",
    "sh": "bash", "shell": "bash", "yml": "yaml", "cs": "csharp", "c++": "cpp",
}


def _extract_code(body: str):
    """Si `body` es un único bloque ```lang\\n...\\n```, devuelve (codigo, lenguaje).
    Si no, devuelve (None, None) y el mensaje se trata como texto normal."""
    match = _FENCE_RE.match(body.strip())
    if not match:
        return None, None
    lang = (match.group(1) or "plaintext").strip().lower()
    code = match.group(2)
    if not code.strip():
        return None, None
    return code, _HLJS_ALIASES.get(lang, lang)


def _resolve_room(kind, target_id):
    """Devuelve (room, objeto_destino) si el usuario actual puede usarla."""
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return None, None

    if kind == "dm":
        other = db.session.get(User, target_id)
        if other and other.id != current_user.id:
            return dm_room(current_user.id, other.id), other
    elif kind == "group":
        grp = db.session.get(Group, target_id)
        # El admin puede entrar a cualquier grupo para moderarlo.
        if grp and (grp.has_member(current_user) or current_user.is_admin):
            return f"group_{grp.id}", grp
    return None, None


def _report_socket_db_error(message: str) -> None:
    db.session.rollback()
    current_app.logger.exception(message)
    emit("error_message", {"error": "No se pudo procesar el chat. Inténtalo de nuevo."})


def _conv_key(kind, target):
    return f"dm_{target.id}" if kind == "dm" else f"group_{target.id}"


@socketio.on("connect")
def handle_connect():
    if not current_user.is_authenticated:
        return False  # rechaza la conexión
    join_room(f"user_{current_user.id}")


@socketio.on("join")
def handle_join(data):
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    try:
        room, target = _resolve_room(data.get("type"), data.get("id"))
        if room:
            # Marca como leído antes de unirse para no añadir el mensaje actual
            # al contador de no leídos.
            ReadMarker.mark_read(current_user.id, _conv_key(data.get("type"), target))
            join_room(room)
            emit("joined", {"room": room})
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al unirse a una sala de chat")


@socketio.on("leave")
def handle_leave(data):
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    try:
        room, _ = _resolve_room(data.get("type"), data.get("id"))
        if room:
            leave_room(room)
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al salir de una sala de chat")


@socketio.on("mark_read")
def handle_mark_read(data):
    """El cliente lo envía cuando llega un mensaje y la conversación está abierta en pantalla:
    así no queda como 'no leído' (contador del icono del chat y barra lateral)."""
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    try:
        room, target = _resolve_room(data.get("type"), data.get("id"))
        if room:
            ReadMarker.mark_read(current_user.id, _conv_key(data.get("type"), target))
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al marcar una conversación como leída")


@socketio.on("delete_message")
def handle_delete_message(data):
    """Solo el administrador puede borrar mensajes (suyos o de cualquier usuario)."""
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    if not current_user.is_admin:
        emit("error_message", {"error": "Solo el administrador puede eliminar mensajes."})
        return
    try:
        message_id = int(data.get("id"))
    except (TypeError, ValueError):
        return
    try:
        msg = db.session.get(Message, message_id)
        if not msg:
            return
        room = f"group_{msg.group_id}" if msg.group_id else dm_room(msg.sender_id, msg.recipient_id)
        db.session.delete(msg)
        db.session.commit()
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al eliminar un mensaje de chat")
        return
    emit("message_deleted", {"id": message_id, "room": room}, to=room)


@socketio.on("typing")
def handle_typing(data):
    """Reenvía el indicador 'X está escribiendo…' a los demás en la sala."""
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    try:
        room, _ = _resolve_room(data.get("type"), data.get("id"))
        if room:
            emit("typing", {"room": room, "username": current_user.username,
                            "user_id": current_user.id}, to=room, include_self=False)
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al validar un evento typing")


@socketio.on("send_message")
def handle_send_message(data):
    if not current_user.is_authenticated or not isinstance(data, dict):
        return
    body = str(data.get("body") or "").strip()
    if not body or len(body) > MAX_LEN:
        emit("error_message", {"error": "Mensaje vacío o demasiado largo (máx. 2000)."})
        return

    kind = data.get("type")
    try:
        sender = db.session.get(User, current_user.get_id())
        if not sender or sender.is_banned:
            emit("error_message", {"error": "Tu sesión ya no está activa. Inicia sesión otra vez."})
            return

        room, target = _resolve_room(kind, data.get("id"))
        if not room or (kind == "dm" and target.is_banned):
            emit("error_message", {"error": "No puedes escribir en esta conversación."})
            return

        code, lang = _extract_code(body)
        is_code = code is not None
        stored_body = code if is_code else body

        if kind == "dm":
            msg = Message(sender=sender, sender_id=sender.id, recipient_id=target.id,
                          body=stored_body,
                          is_code=is_code, code_language=lang)
        else:
            msg = Message(sender=sender, sender_id=sender.id, group_id=target.id,
                          body=stored_body,
                          is_code=is_code, code_language=lang)
        db.session.add(msg)
        db.session.flush()
        payload = msg.to_dict()
        group_member_ids = (
            [member.user_id for member in target.members]
            if kind == "group" else []
        )
        # Committing the read marker also commits the new message in this transaction.
        ReadMarker.mark_read(sender.id, _conv_key(kind, target))
    except SQLAlchemyError:
        _report_socket_db_error("Error de base de datos al guardar un mensaje de chat")
        return

    payload["room"] = room
    emit("new_message", payload, to=room)

    # Aviso a quien no tenga la conversación abierta (badge en la barra lateral)
    if kind == "dm":
        emit("notification", {"key": f"dm_{sender.id}", "from": sender.username,
                              "preview": ("[código] " + body[:60]) if is_code else body[:80]},
             to=f"user_{target.id}")
    else:
        for member_id in group_member_ids:
            if member_id != sender.id:
                emit("notification", {"key": f"group_{target.id}", "from": sender.username,
                                      "preview": ("[código] " + body[:60]) if is_code else body[:80]},
                     to=f"user_{member_id}")
