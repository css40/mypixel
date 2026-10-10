/* Cliente de chat en tiempo real (Socket.IO). Depende de window.CHAT (ver chat/_layout.html).
 *
 * Diseño pensado para no tener lag ni cierres raros:
 *  - Un único socket por página, con reconexión automática y espera creciente.
 *  - Al (re)conectar se pide al servidor lo que se perdió desde el último mensaje visto.
 *  - Los mensajes se envían con "ack": la interfaz sabe si llegaron o fallaron.
 *  - El DOM del chat está acotado (MAX_NODES) y los mensajes se deduplican por id.
 *  - Todo el contenido se inserta con textContent (nunca innerHTML): sin XSS.
 *  - Si el usuario cierra sesión o lo suspenden, el servidor avisa y el socket se cierra.
 */
(function () {
  "use strict";

  if (typeof window.io !== "function") return;     // el script de Socket.IO no cargó (sin red)
  const cfg = window.CHAT || {};

  const list = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const statusEl = document.getElementById("conn-status");
  const typingEl = document.getElementById("typing-indicator");
  const inRoom = Boolean(list && form && input && cfg.kind);

  const MAX_NODES = 300;
  const TYPING_THROTTLE_MS = 1500;
  const ACK_TIMEOUT_MS = 8000;

  const socket = window.io({
    reconnection: true,
    reconnectionAttempts: 40,
    reconnectionDelay: 500,
    reconnectionDelayMax: 5000,
    randomizationFactor: 0.4,
    timeout: 20000,
  });

  // ---- utilidades ---------------------------------------------------------------
  const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" });
  const localTime = (iso) => timeFmt.format(new Date(iso));

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function setStatus(text, state) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.dataset.state = state || "";
  }

  const seen = new Set();                      // ids de mensajes ya pintados
  let lastId = 0;

  function scrollBottom() { if (list) list.scrollTop = list.scrollHeight; }
  const nearBottom = () => list.scrollHeight - list.scrollTop - list.clientHeight < 120;

  // ---- historial renderizado por el servidor --------------------------------------
  if (inRoom) {
    list.querySelectorAll("time[data-ts]").forEach((t) => { t.textContent = localTime(t.dataset.ts); });
    list.querySelectorAll(".msg[data-id]").forEach((li) => {
      const id = Number(li.dataset.id);
      seen.add(id);
      if (id > lastId) lastId = id;
    });
    scrollBottom();
  }

  // ---- construcción de mensajes ---------------------------------------------------
  function renderAvatar(m) {
    let node;
    if (m.sender_avatar) {
      node = el("img", "avatar");
      node.src = m.sender_avatar;
      node.alt = "";
      node.loading = "lazy";
    } else {
      node = el("span", "avatar", m.sender_initial);
    }
    node.style.setProperty("--s", "32px");
    return node;
  }

  function renderCode(m) {
    const lang = m.code_language || "plaintext";
    const fig = el("figure", "code-block");
    const bar = el("figcaption", "code-block__bar");
    const copy = el("button", "btn btn--sm btn--ghost copy-btn", "Copiar");
    copy.type = "button";
    copy.setAttribute("aria-label", "Copiar código al portapapeles");
    bar.append(el("span", null, lang), copy);
    const pre = el("pre");
    const code = el("code", `language-${lang}`, m.body);   // textContent: nunca se interpreta como HTML
    pre.append(code);
    fig.append(bar, pre);
    if (window.MyPixel) window.MyPixel.highlight(fig);
    return fig;
  }

  function renderMessage(m) {
    const mine = m.sender_id === cfg.me;
    const li = el("li", "msg" + (mine ? " msg--mine" : ""));
    li.dataset.id = m.id;

    const body = el("div", "msg__body");
    const meta = el("div", "msg__meta", `${m.sender} · `);
    meta.append(el("time", null, localTime(m.created_at)));
    body.append(meta, m.is_code ? renderCode(m) : el("p", "bubble", m.body));

    li.append(renderAvatar(m), body);
    return li;
  }

  function addMessage(m) {
    if (!inRoom || seen.has(m.id)) return;
    seen.add(m.id);
    if (m.id > lastId) lastId = m.id;

    const stick = nearBottom() || m.sender_id === cfg.me;
    list.appendChild(renderMessage(m));
    while (list.children.length > MAX_NODES) list.firstElementChild.remove();
    if (typingEl) typingEl.textContent = "";
    if (stick) scrollBottom();
  }

  // ---- conexión -------------------------------------------------------------------
  function joinRoom() {
    if (!inRoom) return;
    socket.timeout(ACK_TIMEOUT_MS).emit("join", { type: cfg.kind, id: cfg.targetId, after: lastId }, (err, res) => {
      if (err || !res) { setStatus("Sin respuesta del servidor", "err"); return; }
      if (!res.ok) { setStatus(res.error || "No se pudo entrar a la sala", "err"); return; }
      (res.missed || []).forEach(addMessage);          // lo que llegó mientras no estábamos
      setStatus("En línea", "ok");
    });
  }

  socket.on("connect", () => {
    setStatus(inRoom ? "Entrando…" : "En línea", inRoom ? "" : "ok");
    joinRoom();
  });
  socket.on("disconnect", (reason) => {
    if (reason === "io client disconnect") return;
    setStatus("Reconectando…", "");
  });
  socket.on("connect_error", () => setStatus("Sin conexión", "err"));
  socket.io.on("reconnect_failed", () => setStatus("Conexión perdida: pulsa aquí para reintentar", "err"));

  if (statusEl) statusEl.addEventListener("click", () => { if (!socket.connected) socket.connect(); });
  document.addEventListener("visibilitychange", () => {   // al volver a la pestaña, reintenta si se rindió
    if (document.visibilityState === "visible" && !socket.connected && !socket.active) socket.connect();
  });

  socket.on("session_ended", () => {
    socket.disconnect();
    window.location.href = cfg.loginUrl || "/auth/login";
  });

  // ---- eventos entrantes ----------------------------------------------------------
  socket.on("new_message", addMessage);

  let typingTimer = null;
  socket.on("typing", (data) => {
    if (!inRoom || !typingEl || data.room !== cfg.room) return;
    typingEl.textContent = `${data.username} está escribiendo…`;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => { typingEl.textContent = ""; }, 3000);
  });

  // Punto amarillo en la barra lateral cuando llega algo a otra conversación
  socket.on("notification", (n) => {
    if (n.key === cfg.activeKey) return;
    const link = document.querySelector(`.conv-link[data-conv="${CSS.escape(n.key)}"]`);
    if (link) link.classList.add("has-unread");
  });

  // ---- envío ----------------------------------------------------------------------
  let sending = false;

  function send() {
    const body = input.value.trim();
    if (!body || sending) return;
    if (!socket.connected) { setStatus("Sin conexión: el mensaje no se envió", "err"); return; }

    sending = true;
    sendBtn.disabled = true;
    input.value = "";                                   // envío "optimista": la caja se vacía al instante
    input.style.height = "auto";

    socket.timeout(ACK_TIMEOUT_MS).emit("send_message", { type: cfg.kind, id: cfg.targetId, body }, (err, res) => {
      sending = false;
      sendBtn.disabled = false;
      if (err || !res || !res.ok) {
        if (!input.value) input.value = body;           // devuelve el texto para no perderlo
        setStatus(err ? "El servidor tardó demasiado. Reintenta." : (res && res.error) || "No se pudo enviar.", "err");
        if (res && res.auth === false) window.location.href = cfg.loginUrl || "/auth/login";
      } else {
        setStatus("En línea", "ok");
      }
      input.focus();
    });
  }

  if (inRoom) {
    form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });

    let typingSentAt = 0;
    input.addEventListener("input", () => {            // auto-altura + aviso de "escribiendo"
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 128) + "px";
      const now = Date.now();
      if (socket.connected && now - typingSentAt > TYPING_THROTTLE_MS) {
        socket.emit("typing", { type: cfg.kind, id: cfg.targetId });
        typingSentAt = now;
      }
    });
    input.focus({ preventScroll: true });
  }
})();
