"""Sanitizador HTML propio (allowlist) para el Markdown de los posts.

No usamos `bleach` porque no está disponible en este entorno, así que
implementamos un filtro mínimo con `html.parser.HTMLParser`: cualquier
etiqueta o atributo fuera de la lista blanca se elimina, y cualquier
`href`/`src` con esquema peligroso (`javascript:`, etc.) también.
Esto es lo que hace seguro renderizar Markdown escrito por usuarios con `|safe`.
"""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "em", "b", "i", "s", "del", "code", "pre",
    "blockquote", "ul", "ol", "li", "a", "h1", "h2", "h3", "h4",
    "table", "thead", "tbody", "tr", "th", "td", "span",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "rel", "target"},
    "code": {"class"},   # highlight.js: language-xxxx
    "span": {"class"},
    "th": {"align"}, "td": {"align"},
}
SAFE_SCHEMES = {"http", "https", "mailto"}
VOID_TAGS = {"br", "hr"}


def _safe_url(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme and parsed.scheme.lower() not in SAFE_SCHEMES:
        return None
    return value


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open_stack = []

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_closing=True)

    def _open(self, tag, attrs, self_closing):
        if tag not in ALLOWED_TAGS:
            return
        kept = []
        allowed_attrs = ALLOWED_ATTRS.get(tag, set())
        for name, value in attrs:
            if name not in allowed_attrs or value is None:
                continue
            if name in ("href", "src"):
                safe = _safe_url(value)
                if safe is None:
                    continue
                value = safe
            kept.append(f'{name}="{escape(value, quote=True)}"')
        attr_str = (" " + " ".join(kept)) if kept else ""
        if tag == "a":
            # Enlaces salientes siempre seguros, sin importar lo que venga del post
            attr_str += ' rel="noopener nofollow ugc" target="_blank"'
        if self_closing or tag in VOID_TAGS:
            self.out.append(f"<{tag}{attr_str}>")
        else:
            self.out.append(f"<{tag}{attr_str}>")
            self.open_stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.open_stack:
            # Cierra también cualquier etiqueta abierta después de esta (evita HTML roto)
            while self.open_stack and self.open_stack[-1] != tag:
                self.out.append(f"</{self.open_stack.pop()}>")
            if self.open_stack:
                self.out.append(f"</{self.open_stack.pop()}>")

    def handle_data(self, data):
        self.out.append(escape(data))

    def close(self):
        super().close()
        while self.open_stack:
            self.out.append(f"</{self.open_stack.pop()}>")

    def get_html(self) -> str:
        return "".join(self.out)


def sanitize_html(raw_html: str) -> str:
    parser = _Sanitizer()
    parser.feed(raw_html)
    parser.close()
    return parser.get_html()
