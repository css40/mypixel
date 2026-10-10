# MyPixel

Red social para compartir fragmentos de código con **chat en tiempo real** y una interfaz **Retro Pixel Art** inspirada en las máquinas arcade de 8/16 bits. Publica snippets con resaltado de sintaxis, valóralos, comenta, sigue a otros desarrolladores y chatea en grupos o en privado.

## Características principales

- **Diseño Retro Pixel Art completo**: tipografías *Press Start 2P* y *VT323*, fondo oscuro con cuadrícula de píxeles, verdes fosforescentes y neones, bordes gruesos con sombra en bloque, botones 8-bit con efecto "press", sprites SVG propios (invader, corazón, estrella) y efecto de líneas de barrido CRT. Sin Tailwind ni compilación en el navegador: una sola hoja de estilos (`pixel.css`).
- **Chat en tiempo real** (Socket.IO): mensajes directos y grupos, indicador "escribiendo…", avisos de no leídos persistentes, bloques de código resaltados con botón *Copiar* (envuelve el mensaje entre ```` ``` ````).
- **Chat robusto**: reconexión automática con recuperación de los mensajes perdidos, confirmación de entrega (ack), deduplicación, DOM acotado y cierre de sockets al cerrar sesión o suspender una cuenta.
- **Publicaciones**: Markdown seguro (sanitizado), hasta 5 bloques de código por post, etiquetas, adjuntos, "me gusta" y valoraciones de 1 a 5 estrellas con ranking bayesiano.
- **Perfiles y notificaciones**: avatares, seguidores, alertas de comentarios, likes y valoraciones.
- **Panel de administración**: estadísticas, moderación de usuarios y publicaciones.
- **Rendimiento**: consultas agrupadas (sin N+1), índices compuestos, caché de vida corta para la barra lateral y pool de conexiones con `pool_pre_ping` para bases de datos remotas.

## Estructura del proyecto

```
mypixel/
├── run.py                  # Punto de entrada (python run.py)
├── config.py               # Configuración (desarrollo / producción / tests)
├── seed.py                 # Datos de ejemplo (solo SQLite local)
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py         # Application factory: create_app()
│   ├── extensions.py       # db, login, csrf, socketio
│   ├── models.py           # Modelos SQLAlchemy e índices
│   ├── db_tools.py         # Índices idempotentes y cuenta admin al arrancar
│   ├── utils.py            # Subidas seguras, Markdown, helpers
│   ├── sanitize.py         # Sanitizador HTML (lista blanca)
│   ├── services/           # Lógica de negocio (capa de "controladores")
│   │   ├── chat_service.py #   salas, historial, barra lateral, envío
│   │   └── post_service.py #   feed, contadores agregados, cachés
│   ├── main/               # Archivos subidos, filtros Jinja, páginas de error
│   ├── auth/               # Registro, login, logout
│   ├── posts/              # Feed, CRUD, comentarios, likes, valoraciones
│   ├── profile/            # Perfiles, seguidores, edición
│   ├── chat/               # routes.py (HTTP) y events.py (Socket.IO)
│   ├── admin/              # Panel de administración
│   ├── templates/          # Frontend: plantillas Jinja2 por módulo
│   └── static/
│       ├── css/pixel.css   # Todo el estilo Retro Pixel Art
│       ├── js/             # main.js, chat.js, post_form.js
│       └── img/            # favicon.svg
├── uploads/                # Avatares y adjuntos (no se versiona)
└── instance/               # Base SQLite local (no se versiona)
```

Las rutas y los eventos son capas finas: validan la petición y delegan en `app/services/`.

## Ejecutar en local

Requisitos: **Python 3.12 o superior** (probado con 3.14) y `pip`.

1. **Entra a la carpeta** del proyecto:
   ```bash
   cd mypixel
   ```
2. **Crea y activa un entorno virtual**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   ```
3. **Instala las dependencias**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Configura el entorno**:
   ```bash
   cp .env.example .env               # Windows: copy .env.example .env
   ```
   Edita `.env`: pon una `SECRET_KEY` y, si quieres cuenta de administrador, un `ADMIN_PASSWORD`. Deja `DATABASE_URL` comentada para usar SQLite en local.
5. **(Opcional) Carga datos de ejemplo**:
   ```bash
   python seed.py
   ```
   Crea los usuarios `ana`, `beto` y `carla` (contraseña `mypixel1234`), tres posts y un grupo. Solo funciona con SQLite.
6. **Arranca la aplicación**:
   ```bash
   python run.py
   ```
7. Abre <http://127.0.0.1:5000>. Para probar el chat, abre una segunda ventana en modo incógnito con otro usuario.

Las tablas y los índices se crean automáticamente al arrancar. Si defines `ADMIN_PASSWORD`, la primera ejecución crea la cuenta de administrador con `ADMIN_EMAIL`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave de sesión. Obligatoria en producción. |
| `DATABASE_URL` | Base de datos. Sin definir: SQLite local. Acepta `postgresql://` y `postgres://`. |
| `APP_ENV` | `development` o `production`. Si falta, una URL PostgreSQL implica producción. |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Cuenta admin inicial (solo se crea si hay contraseña). |
| `HOST`, `PORT` | Dirección y puerto de `run.py` (por defecto `127.0.0.1:5000`). |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | Tamaño del pool de conexiones PostgreSQL (5 y 5). |
| `AUTO_CREATE_DB` | `0` para no crear tablas/índices al arrancar. |

## Notas técnicas

- El chat usa Socket.IO sin cola de mensajes, así que debe ejecutarse en **un único proceso**. Para escalar a varios procesos haría falta añadir un `message_queue` (Redis).
- Los estáticos llevan `?v=<versión>` y se cachean 30 días; cambian solos cuando editas CSS o JS.
