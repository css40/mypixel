# MyPixel → Render (cambio de base de datos local a Render)

La app decide la base de datos **solo con la variable `DATABASE_URL`**:

| `DATABASE_URL`            | Base de datos usada                         |
|---------------------------|---------------------------------------------|
| vacía / no existe         | SQLite local → `instance/mypixel.db`        |
| `postgresql://…` (Render) | PostgreSQL de Render                        |

El código ya convierte `postgres://` en `postgresql+psycopg://` y añade `sslmode=require`
cuando usas la URL externa de Render. Al arrancar crea las tablas, añade las columnas nuevas
si faltan y crea el admin si todavía no hay ninguno.

## 1) Probar en tu PC contra la base de Render (antes de subir nada)

1. En Render crea la base: **New → PostgreSQL** (nombre `mypixel-db`).
2. Copia la **External Database URL** (Connections).
3. En tu `.env` pon:
   ```
   DATABASE_URL=postgresql://USUARIO:CLAVE@dpg-xxxx.oregon-postgres.render.com/mypixel
   ```
4. `pip install -r requirements.txt` y `python run.py` → abre http://127.0.0.1:5000
   (sigue en modo desarrollo, con `http`; no hace falta HTTPS).
5. Inicia sesión como admin con **correo** `admin@mypixel.com` y la contraseña de `ADMIN_PASSWORD`.
6. Para volver a la base local: comenta la línea `DATABASE_URL` del `.env`.

> Tus datos locales (`instance/mypixel.db`) **no** se copian solos: la base de Render empieza vacía
> (solo con el admin). Si quieres conservar usuarios o posts locales, dímelo y preparo un script de copia.

## 2) Subir a Render

**Con Blueprint (lo más rápido):** sube el proyecto a GitHub → Render → **New → Blueprint** → elige el repo.
`render.yaml` crea la base de datos y el servicio web, y conecta `DATABASE_URL` con la URL *interna*.
Render te pide `ADMIN_PASSWORD`: pon una contraseña propia y larga.

**A mano:** New → Web Service →
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --worker-class gthread --threads 100 --workers 1 --bind 0.0.0.0:$PORT run:app`
- Variables: `DATABASE_URL` (Internal Database URL), `SECRET_KEY` (aleatoria), `ADMIN_EMAIL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`.

## Cosas a tener en cuenta

- **Archivos subidos** (avatares con imagen y adjuntos de posts) se guardan en disco. En Render el disco del
  plan gratuito es **efímero**: se borran en cada despliegue/reinicio. Los avatares retro (emoji) no tienen este
  problema porque viven en la base de datos. Para conservar imágenes hace falta un *Persistent Disk* (plan de pago)
  o guardarlas en la base de datos / un almacenamiento externo.
- Usa **1 solo worker** de gunicorn (el chat en tiempo real lo necesita con la configuración actual).
- Cambia `ADMIN_PASSWORD`: el valor por defecto está escrito en el código.
