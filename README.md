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
├── uploads/            # Compatibilidad con archivos locales antiguos
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
- Las nuevas imágenes se almacenan en ImgBB; los registros locales antiguos se siguen sirviendo desde `uploads/`.

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
| `IMGBB_API_KEY` | Clave API de ImgBB para almacenar imágenes; configúrala como secreto y nunca la incluyas en el repositorio. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Credenciales OAuth de Google. Callback: `/authorize/google`. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Credenciales OAuth de GitHub. Callback: `/authorize/github`. |
| `OAUTH_REDIRECT_BASE_URL` | Origen HTTPS usado para los callbacks; por defecto `https://mypixel-3po7.onrender.com`. |
| `RENDER` | Render la define en el servicio; activa la configuración de producción. |

La cuenta administradora se crea al iniciar la aplicación si todavía no existe una cuenta administradora. Cambiar las variables después no cambia la contraseña de una cuenta ya existente.

### Inicio de sesión con Google y GitHub

Configura las credenciales OAuth de cada proveedor en tu entorno local o en Render. Registra en la consola del proveedor la URL pública exacta de callback:

- Google: `https://mypixel-3po7.onrender.com/authorize/google`
- GitHub: `https://mypixel-3po7.onrender.com/authorize/github`

Google debe devolver `email_verified=true`. Para GitHub, la app solicita `read:user` y `user:email` y solo acepta direcciones verificadas. El usuario nuevo se crea con correo verificado y contraseña aleatoria no conocida; un usuario existente con el mismo correo inicia sesión en su cuenta.

### Almacenamiento de imágenes y archivos

Los avatares y las imágenes adjuntas nuevas se suben a ImgBB y su URL HTTPS se guarda en la base de datos. Configura `IMGBB_API_KEY` en tu entorno local o en Render antes de permitir subidas de imágenes. Los adjuntos que no son imágenes y los archivos locales existentes siguen usando el disco local; pueden perderse si ese disco es efímero.

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
