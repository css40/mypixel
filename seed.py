"""Datos de ejemplo para desarrollo local:  python seed.py
Usuarios de prueba: ana / beto / carla, contraseña mypixel1234.

Por seguridad solo se ejecuta contra SQLite; si DATABASE_URL apunta a otra base de
datos (por ejemplo la de producción) se niega a escribir datos de prueba."""
import sys

from app import create_app
from app.extensions import db
from config import Config
from app.models import CodeBlock, Comment, Follow, Group, GroupMember, Like, Post, Rating, Tag, User

# La comprobación va ANTES de crear la app: create_app() ya toca la base de datos.
if not Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
    sys.exit("seed.py solo funciona con SQLite (desarrollo local). Quita DATABASE_URL de tu .env.")

app = create_app("development")

with app.app_context():
    if User.query.filter(User.is_admin.is_(False)).first():
        print("La base de datos ya tiene datos de ejemplo; no se hace nada.")
        raise SystemExit

    ana = User(username="ana", email="ana@example.com", bio="Backend con Python y Flask.",
               github_url="https://github.com/ana")
    beto = User(username="beto", email="beto@example.com", bio="Me gusta SQL y los índices.")
    carla = User(username="carla", email="carla@example.com", bio="Frontend, React y accesibilidad.")
    for u in (ana, beto, carla):
        u.set_password("mypixel1234")
    db.session.add_all([ana, beto, carla])
    db.session.flush()

    p1 = Post(title="Leer un archivo grande línea por línea", language="Python", author=ana,
              body="Evita cargar todo el archivo en memoria: itera directamente sobre el "
                   "objeto archivo. Es la forma **más eficiente** en memoria y funciona "
                   "igual de bien con archivos de unos KB que de varios GB.\n\n"
                   "> Truco: combínalo con `itertools.islice` si solo necesitas las primeras N líneas.")
    p1.code_blocks.append(CodeBlock(language="Python", position=0, label="lectura.py", code=(
        "with open('datos.log', encoding='utf-8') as f:\n"
        "    for linea in f:\n"
        "        procesar(linea.rstrip())")))
    p1.tags = [Tag.get_or_create("python"), Tag.get_or_create("io"), Tag.get_or_create("buenas-practicas")]

    p2 = Post(title="Top 3 clientes por facturación", language="SQL", author=beto,
              body="Una CTE con función de ventana resuelve el ranking sin subconsultas anidadas.\n\n"
                   "Funciona igual en PostgreSQL, MySQL 8+ y SQLite moderno.")
    p2.code_blocks.append(CodeBlock(language="SQL", position=0, label="ranking.sql", code=(
        "WITH ranking AS (\n"
        "  SELECT cliente_id, SUM(total) AS facturado,\n"
        "         RANK() OVER (ORDER BY SUM(total) DESC) AS pos\n"
        "  FROM pedidos\n"
        "  GROUP BY cliente_id\n"
        ")\n"
        "SELECT * FROM ranking WHERE pos <= 3;")))
    p2.tags = [Tag.get_or_create("sql"), Tag.get_or_create("window-functions")]

    p3 = Post(title="Antes/después: debounce de un input de búsqueda", language="JavaScript",
              author=carla, body="Sin *debounce*, cada tecla dispara una petición. Con un "
              "debounce de 300 ms se agrupan las pulsaciones y se hace una sola llamada.")
    p3.code_blocks.append(CodeBlock(language="JavaScript", position=0, label="Antes", code=(
        "input.addEventListener('input', (e) => buscar(e.target.value));")))
    p3.code_blocks.append(CodeBlock(language="JavaScript", position=1, label="Después", code=(
        "function debounce(fn, ms) {\n"
        "  let t;\n"
        "  return (...args) => {\n"
        "    clearTimeout(t);\n"
        "    t = setTimeout(() => fn(...args), ms);\n"
        "  };\n"
        "}\n"
        "input.addEventListener('input', debounce((e) => buscar(e.target.value), 300));")))
    p3.tags = [Tag.get_or_create("javascript"), Tag.get_or_create("performance")]

    db.session.add_all([p1, p2, p3])
    db.session.flush()

    db.session.add_all([
        Comment(body="¡Muy útil! También puedes usar itertools.islice para paginar.", author=beto, post=p1),
        Comment(body="Buen ejemplo, el RANK() evita un self-join feo.", author=ana, post=p2),
        Like(user_id=beto.id, post_id=p1.id),
        Like(user_id=carla.id, post_id=p1.id),
        Like(user_id=ana.id, post_id=p2.id),
        Rating(user_id=beto.id, post_id=p1.id, score=5),
        Rating(user_id=carla.id, post_id=p1.id, score=5),
        Rating(user_id=ana.id, post_id=p2.id, score=4),
        Rating(user_id=carla.id, post_id=p2.id, score=5),
        Rating(user_id=ana.id, post_id=p3.id, score=5),
        Follow(follower_id=beto.id, followed_id=ana.id),
        Follow(follower_id=carla.id, followed_id=ana.id),
    ])

    grp = Group(name="python-backend", description="Flask, FastAPI y buenas prácticas", owner_id=ana.id)
    grp.members.append(GroupMember(user_id=ana.id, role="admin"))
    grp.members.append(GroupMember(user_id=beto.id))
    db.session.add(grp)
    db.session.commit()
    print("Listo: usuarios ana, beto y carla (contraseña mypixel1234), 3 posts, valoraciones y un grupo.")
