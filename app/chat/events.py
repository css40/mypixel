"""Eventos de Flask-SocketIO (capa fina: validan, delegan en el servicio y emiten).

Salas:
  user_<id>      -> sala personal (notificaciones y cierre de sesión)
  dm_<a>_<b>     -> chat directo entre dos usuarios (a < b)
  group_<id>     -> chat de grupo

El servidor SIEMPRE valida permisos: un cliente no puede unirse ni escribir en una
sala que no le corresponde aunque manipule los eventos desde el navegador. Todos los
manejadores devuelven un diccionario que Socket.IO envía como "ack" al cliente, así
la interfaz sabe si el mensaje llegó o falló (sin eventos de error sueltos).
"""
from functools import wraps

from flask import current_app
from flask_login import current_user
from flask_socketio import emit, join_room, leave_room, rooms
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db, socketio
from ..models import dm_room
from ..services import chat_service as chat

DB_ERROR = "No se pudo procesar el chat. Inténtalo de nuevo."


def user_room(user_id) -> str:
    return f"user_{user_id}"


def _room_name(kind, target_id):
    """Nombre de sala SIN tocar la base de datos (la pertenencia ya se validó al unirse)."""
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return None
    if kind == "dm":
        return dm_room(current_user.id, target_id)
    if kind == "group":
        return f"group_{target_id}"
    return None


def socket_handler(view):
    """Exige sesión válida y payload dict; convierte errores de BD en un ack limpio."""
    @wraps(view)
    def wrapper(data=None):
        if not current_user.is_authenticated:
            return {"ok": False, "error": "Tu sesión ya no está activa. Inicia sesión otra vez.",
                    "auth": False}
        if not isinstance(data, dict):
            return {"ok": False, "error": "Petición inválida."}
        try:
            return view(data)
        except chat.ChatError as exc:
            return {"ok": False, "error": str(exc)}
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Error de base de datos en el evento %s", view.__name__)
            return {"ok": False, "error": DB_ERROR}
    return wrapper


@socketio.on("connect")
def on_connect():
    if not current_user.is_authenticated:
        return False  # rechaza la conexión
    join_room(user_room(current_user.id))


@socketio.on("join")
@socket_handler
def on_join(data):
    kind = data.get("type")
    room, target = chat.resolve_target(kind, data.get("id"), current_user)
    if not room:
        return {"ok": False, "error": "No puedes acceder a esta conversación."}
    chat.mark_read(current_user.id, chat.conv_key(kind, target.id))
    join_room(room)

    # Recupera lo que se envió mientras el cliente estaba desconectado (o entre que
    # se renderizó la página y se abrió el socket).
    missed = []
    after = data.get("after")
    if isinstance(after, int) and after >= 0:
        missed = chat.messages_after(current_user.id, kind, target.id, after)
    return {"ok": True, "room": room, "missed": missed}


@socketio.on("leave")
@socket_handler
def on_leave(data):
    room = _room_name(data.get("type"), data.get("id"))
    if room:
        leave_room(room)
    return {"ok": True}


@socketio.on("typing")
@socket_handler
def on_typing(data):
    """Reenvía 'X está escribiendo…'. No consulta la BD: basta con comprobar que este
    socket ya está dentro de la sala (se validó en `join`)."""
    room = _room_name(data.get("type"), data.get("id"))
    if room and room in rooms():
        emit("typing", {"room": room, "username": current_user.username,
                        "user_id": current_user.id}, to=room, include_self=False)
    return {"ok": True}


@socketio.on("send_message")
@socket_handler
def on_send_message(data):
    sender = current_user._get_current_object()
    sent = chat.create_message(sender, data.get("type"), data.get("id"), data.get("body"))

    emit("new_message", sent.payload, to=sent.room)

    # Aviso a quien no tenga la conversación abierta (punto en la barra lateral)
    note = {"key": sent.notify_key, "from": sent.payload["sender"], "preview": sent.preview}
    for uid in sent.notify_ids:
        emit("notification", note, to=user_room(uid))
    return {"ok": True, "id": sent.payload["id"]}


def end_user_sessions(user_id) -> None:
    """Avisa a todos los sockets de un usuario de que su sesión terminó (logout o
    suspensión). El cliente se desconecta y vuelve al login."""
    try:
        socketio.emit("session_ended", {}, to=user_room(user_id))
    except Exception:  # nunca debe romper la petición HTTP que lo origina
        current_app.logger.warning("No se pudo avisar del cierre de sesión", exc_info=True)
