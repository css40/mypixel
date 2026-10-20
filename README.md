# MyPixel

MyPixel es una aplicación web en español para compartir publicaciones de código. Está construida con Flask y SQLAlchemy; incluye cuentas, perfiles, publicaciones, comentarios, chat en tiempo real y un panel de administración.

## Estructura del proyecto

```text
mypixel/
├── app/
│   ├── admin/          # Panel de administración y sus rutas
│   ├── auth/           # Registro, inicio de sesión y formularios de autenticación
│   ├── chat/           # Salas, grupos y eventos de Socket.IO
│   ├── main/           # Páginas generales, como Acerca de y notificaciones
│   ├── posts/          # Publicaciones, comentarios, reacciones y formularios
│   ├── profile/        # Perfiles y edición de perfil
│   ├── static/         # CSS, JavaScript e imágenes
│   ├── templates/      # Plantillas Jinja organizadas por funcionalidad
│   ├── extensions.py   # Extensiones Flask compartidas
│   ├── models.py       # Modelos y relaciones de SQLAlchemy
│   ├── sanitize.py     # Limpieza y validación de contenido
│   └── utils.py        # Funciones auxiliares
├── instance/           # Base de datos SQLite local (se crea localmente)
├── uploads/
│   ├── avatars/        # Avatares cargados por usuarios
│   └── attachments/    # Imágenes y archivos adjuntos de publicaciones
├── config.py           # Configuración, variables de entorno y base de datos
├── run.py              # Punto de entrada de la aplicación
├── seed.py             # Datos de ejemplo para desarrollo local
├── requirements.txt    # Dependencias Python
├── render.yaml         # Blueprint de Render (servicio y PostgreSQL)
└── DEPLOY_RENDER.md    # Instrucciones detalladas para desplegar en Render
```

### Cómo se conecta la aplicación

- `run.py` crea la aplicación mediante `create_app()` y expone `app` para Gunicorn.
- `app/__init__.py` configura Flask, inicializa las extensiones, registra los blueprints y prepara las tablas.
- `config.py` carga `.env`. Sin `DATABASE_URL`, usa SQLite en `instance/mypixel.db`; con ella, conecta a la base indicada, normalmente PostgreSQL en Render.
- `app/extensions.py` centraliza SQLAlchemy, Flask-Login, CSRF y Socket.IO. El chat usa el modo `threading`.
- `app/templates/` contiene las páginas HTML; los estilos y scripts están en `app/static/`.
- `uploads/` guarda archivos de usuarios en el sistema de archivos del servidor.

## Ejecutar localmente

Requiere Python y `pip`.

1. Crea y activa un entorno virtual:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

   En Windows PowerShell, activa el entorno con:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

2. Instala dependencias y prepara la configuración local:

   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   ```

3. Edita `.env`: configura una `SECRET_KEY` aleatoria y define una contraseña propia con `ADMIN_PASSWORD`. Deja `DATABASE_URL` sin configurar para usar SQLite local.
4. Inicia la aplicación:

   ```bash
   python run.py
   ```

   Abre <http://127.0.0.1:5000>.

Para cargar publicaciones y usuarios de demostración en la base local, ejecuta `python seed.py`. No ejecutes el seeder en producción.

## Publicar en Render

La forma recomendada es conectar el repositorio a Render como Blueprint: **New → Blueprint**, seleccionar el repositorio y revisar los recursos declarados en `render.yaml`. El Blueprint configura PostgreSQL y el servicio web, y Render solicita `ADMIN_PASSWORD`.

La configuración actual del servicio es:

- **Build:** `pip install -r requirements.txt`
- **Start:** `gunicorn --worker-class gthread --threads 100 --workers 1 --bind 0.0.0.0:$PORT run:app`
- **Base de datos:** PostgreSQL mediante `DATABASE_URL`.
- **Health check:** `/auth/login`.

Mantén **un solo worker** de Gunicorn: el chat en tiempo real está configurado con Socket.IO en modo `threading`.

### Variables de entorno importantes

| Variable | Uso |
| --- | --- |
| `DATABASE_URL` | URL de PostgreSQL en Render. El Blueprint la conecta automáticamente. |
| `SECRET_KEY` | Clave secreta para sesiones y seguridad de Flask. En Render, usa un valor secreto generado. |
| `ADMIN_USERNAME` | Nombre de la cuenta administradora inicial. |
| `ADMIN_EMAIL` | Correo de la cuenta administradora inicial; se usa para iniciar sesión. |
| `ADMIN_PASSWORD` | Contraseña para crear la cuenta administradora si aún no existe. Configúrala en Render, nunca en el repositorio. |
| `RENDER` | Render la define en el servicio; activa la configuración de producción. |

La cuenta administradora se crea al iniciar la aplicación si todavía no existe una cuenta administradora. Cambiar las variables después no cambia la contraseña de una cuenta ya existente.

### Almacenamiento de imágenes y archivos

Los avatares cargados y adjuntos se guardan en `uploads/`. El disco del servicio Render puede ser efímero: esos archivos pueden perderse al reiniciar o desplegar. Si deben conservarse, configura un disco persistente o integra almacenamiento externo y actualiza la lógica de carga/lectura en `config.py` y las rutas que gestionan esos archivos.

La base de datos y los archivos subidos son almacenamientos separados. Los datos de SQLite local no se copian automáticamente a PostgreSQL en Render; se necesita una migración o importación explícita si se desean conservar.

## Guía para cambios futuros y para asistentes de IA

Antes de modificar o desplegar el proyecto:

1. Identifica la funcionalidad afectada y revisa su blueprint en `app/` y sus plantillas relacionadas.
2. Si cambian tablas o relaciones, revisa `app/models.py` y cómo se inicializa la base en `app/__init__.py`. `db.create_all()` crea tablas nuevas, pero no es un sistema general de migraciones para tablas ya existentes.
3. Si cambian dependencias, actualiza `requirements.txt`.
4. Si cambia el arranque, la base de datos, las variables o los recursos de producción, revisa `run.py`, `config.py`, `render.yaml` y esta guía.
5. No incluyas `.env`, contraseñas, claves ni URLs de base de datos con credenciales en Git. Usa `.env.example` solo para nombres y ejemplos no secretos.
6. Comprueba el flujo local y la configuración Blueprint antes de desplegar; considera también el disco efímero de Render y el uso de un solo worker para Socket.IO.

Para instrucciones paso a paso y opciones manuales, consulta [DEPLOY_RENDER.md](./DEPLOY_RENDER.md).
