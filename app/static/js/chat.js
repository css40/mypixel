/* Cliente de chat en tiempo real (Socket.IO). Depende de window.CHAT (ver _layout.html).
 * Si el mensaje entero es un bloque ```lenguaje ... ``` el servidor lo guarda como
 * código (is_code=true) y aquí se muestra con resaltado y botón "Copiar", igual
 * que en los posts, en vez de como una burbuja de chat normal. */
(function () {
  "use strict";
  const cfg = window.CHAT || {};
  const socket = io();                       // mismo origen; la cookie de sesión autentica

  const list = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const status = document.getElementById("conn-status");
  const typingEl = document.getElementById("typing-indicator");
  const inRoom = Boolean(list && form && cfg.kind);

  const fmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" });
  const localTime = (iso) => fmt.format(new Date(iso));

  function scrollBottom() { if (list) list.scrollTop = list.scrollHeight; }
  function setStatus(text, ok) {
    if (!status) return;
    status.textContent = text.toUpperCase();
    status.style.color = ok ? "#1d1b25" : "";
  }
  function highlightNew(root) {
    if (window.hljs) root.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));
  }

  // Formatea las horas del historial renderizado por el servidor y resalta su código
  document.querySelectorAll("#messages time[data-ts]").forEach((t) => { t.textContent = localTime(t.dataset.ts); });
  if (list) highlightNew(list);
  scrollBottom();

  // ---- Construcción de un mensaje entrante (sin innerHTML con datos del usuario => sin XSS) ----
  function renderAvatar(m) {
    let av;
    if (m.sender_avatar) {
      av = document.createElement("img");
      av.src = m.sender_avatar; av.alt = "";
    } else {
      av = document.createElement("span");
      av.textContent = m.sender_emoji || "👾";
      av.style.setProperty("--bg", /^#[0-9a-fA-F]{3,8}$/.test(m.sender_color || "") ? m.sender_color : "#a35a5a");
      av.setAttribute("aria-hidden", "true");
    }
    av.className = "av";
    av.style.setProperty("--s", "30px");
    return av;
  }

  // Botón ✕ para que el admin elimine un mensaje (suyo o de cualquier usuario)
  function deleteButton(id) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "msg-del"; b.dataset.del = id;
    b.title = "Eliminar mensaje"; b.setAttribute("aria-label", "Eliminar mensaje");
    b.textContent = "✕";
    return b;
  }

  function renderMessage(m) {
    const mine = m.sender_id === cfg.me;
    const li = document.createElement("li");
    li.className = "msg" + (mine ? " me" : "");
    li.dataset.id = m.id;

    const col = document.createElement("div");
    col.className = "col";
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = m.sender;
    const time = document.createElement("time");
    time.textContent = localTime(m.created_at);
    meta.appendChild(time);
    if (cfg.isAdmin) meta.appendChild(deleteButton(m.id));
    col.appendChild(meta);

    if (m.is_code) {
      col.appendChild(renderCodeBlock(m));
    } else {
      const bb = document.createElement("div");
      bb.className = "bb";
      bb.textContent = m.body;
      col.appendChild(bb);
    }

    li.append(renderAvatar(m), col);
    return li;
  }

  // Reutiliza la misma estructura que los bloques de código de los posts (ver _macros.html)
  function renderCodeBlock(m) {
    const holder = document.createElement("div");
    holder.className = "chat-code";
    holder.innerHTML =
      '<figure class="code-block">' +
      '<figcaption><span></span>' +
      '<button type="button" class="copy-btn" aria-label="Copiar código al portapapeles">COPIAR</button>' +
      '</figcaption><pre><code></code></pre></figure>';
    holder.querySelector("figcaption span").textContent = m.code_language || "plaintext";
    const codeEl = holder.querySelector("code");
    codeEl.className = "language-" + (m.code_language || "plaintext");
    codeEl.textContent = m.body;             // textContent: nunca se interpreta como HTML
    highlightNew(holder);
    return holder;
  }

  // ---- Conexión ----
  socket.on("connect", () => {
    setStatus("En línea", true);
    if (inRoom) socket.emit("join", { type: cfg.kind, id: cfg.targetId });
  });
  socket.on("disconnect", () => setStatus("Reconectando…", false));
  socket.on("connect_error", () => setStatus("Sin conexión", false));

  // ---- Mensajes entrantes ----
  socket.on("new_message", (m) => {
    if (!inRoom || list.querySelector(`[data-id="${m.id}"]`)) return;
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;
    list.appendChild(renderMessage(m));
    if (typingEl) typingEl.classList.add("hidden");
    // La conversación está abierta: se da por leído (campana y barra lateral)
    if (m.sender_id !== cfg.me) socket.emit("mark_read", { type: cfg.kind, id: cfg.targetId });
    if (nearBottom || m.sender_id === cfg.me) scrollBottom();
  });

  socket.on("error_message", (e) => setStatus(e.error, false));

  // ---- Moderación: el admin elimina mensajes ----
  socket.on("message_deleted", (d) => {
    const li = list && list.querySelector(`[data-id="${d.id}"]`);
    if (li) li.remove();
  });
  if (list && cfg.isAdmin) {
    list.addEventListener("click", (e) => {
      const btn = e.target.closest(".msg-del");
      if (!btn || !window.confirm("¿Eliminar este mensaje para todos?")) return;
      btn.closest(".msg").classList.add("is-removing");
      socket.emit("delete_message", { id: Number(btn.dataset.del) });
    });
  }

  // ---- Indicador "X está escribiendo…" ----
  let typingResetTimer = null;
  socket.on("typing", (data) => {
    if (!inRoom || !typingEl) return;
    typingEl.textContent = `${data.username} está escribiendo…`;
    typingEl.classList.remove("hidden");
    clearTimeout(typingResetTimer);
    typingResetTimer = setTimeout(() => typingEl.classList.add("hidden"), 3000);
  });

  // ---- Notificaciones de otras conversaciones (punto ámbar en la barra lateral) ----
  function bumpBell() {
    const bell = document.querySelector('.nb-icon[aria-label="Notificaciones"]');
    if (!bell) return;
    let c = bell.querySelector(".count");
    if (!c) { c = document.createElement("span"); c.className = "count"; c.textContent = "0"; bell.appendChild(c); }
    const n = parseInt(c.textContent, 10);
    c.textContent = n >= 99 ? "99+" : String((isNaN(n) ? 0 : n) + 1);
  }

  socket.on("notification", (n) => {
    if (n.key === cfg.activeKey) return;
    if (n.key.startsWith("dm_")) bumpBell();
    const link = document.querySelector(`.conv-link[data-conv="${n.key}"]`);
    if (link) {
      link.classList.add("has-unread");
      const badge = link.querySelector(".badge");
      if (badge) badge.classList.remove("hidden");
    }
  });

  // ---- Enviar ----
  function send() {
    const body = input.value.trim();
    if (!body) return;
    socket.emit("send_message", { type: cfg.kind, id: cfg.targetId, body });
    input.value = "";
    input.style.height = "auto";
  }

  if (inRoom) {
    form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });

    let typingSentAt = 0;
    input.addEventListener("input", () => {           // auto-altura + aviso de "escribiendo"
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 128) + "px";
      const now = Date.now();
      if (now - typingSentAt > 1500) {                 // no saturar el servidor de eventos
        socket.emit("typing", { type: cfg.kind, id: cfg.targetId });
        typingSentAt = now;
      }
    });
    input.focus();
  }
})();
