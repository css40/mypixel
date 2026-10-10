"""Punto de entrada. Ejecuta:  python run.py"""
import os

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
        debug=app.debug,
        allow_unsafe_werkzeug=True,   # servidor de desarrollo; no usar así de cara al público
    )
