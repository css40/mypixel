"""Punto de entrada. Ejecuta:  python run.py"""
from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    # Para desarrollo. En producción usa gunicorn/eventlet (ver README).
    socketio.run(app, host="127.0.0.1", port=5000, debug=app.debug,
                 allow_unsafe_werkzeug=True)
