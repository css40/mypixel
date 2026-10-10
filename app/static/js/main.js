/* JS global de MyPixel: resaltado de código, "Copiar", likes, valoraciones y
 * protección contra envíos dobles. Se carga con `defer`, así que el DOM ya existe. */
(function () {
  "use strict";

  const themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    const updateThemeButton = (theme) => {
      const nextTheme = theme === "dark" ? "light" : "dark";
      const label = nextTheme === "light" ? "modo claro" : "modo oscuro";
      themeToggle.textContent = `Modo ${nextTheme === "light" ? "claro" : "oscuro"}`;
      themeToggle.setAttribute("aria-label", `Cambiar a ${label}`);
      themeToggle.title = `Cambiar a ${label}`;
    };

    const initialTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    updateThemeButton(initialTheme);
    themeToggle.addEventListener("click", () => {
      const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      document.querySelector('meta[name="theme-color"]')?.setAttribute(
        "content",
        nextTheme === "light" ? "#e7e4d8" : "#22251f",
      );
      updateThemeButton(nextTheme);
      try {
        window.localStorage.setItem("mypixel-theme", nextTheme);
      } catch (error) {
        console.warn("No se pudo guardar la preferencia de tema.", error);
      }
    });
  }

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

  // ---- 1. Resaltado de sintaxis (highlight.js) --------------------------------
  function highlight(root) {
    if (!window.hljs) return;
    (root || document).querySelectorAll("pre code:not(.hljs)").forEach((el) => {
      const lang = (el.className.match(/language-([\w+#.\-]+)/) || [])[1];
      if (lang && !window.hljs.getLanguage(lang)) el.className = "language-plaintext";
      window.hljs.highlightElement(el);
    });
  }
  window.MyPixel = { highlight, csrfToken };
  highlight(document);

  // ---- 2. Botón "Copiar código" (delegación de eventos) -----------------------
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");   // respaldo para http:// o navegadores viejos
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".copy-btn");
    if (!btn) return;
    const code = btn.closest(".code-block")?.querySelector("code");
    if (!code) return;
    const original = btn.dataset.label || btn.textContent;
    btn.dataset.label = original;
    try {
      await copyText(code.textContent);
      btn.textContent = "¡Copiado!";
      btn.classList.add("is-copied");
    } catch (err) {
      btn.textContent = "Error";
    }
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.textContent = original; btn.classList.remove("is-copied"); }, 1600);
  });

  // ---- 3. Me gusta (AJAX) -----------------------------------------------------
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".like-btn");
    if (!btn) return;
    if (btn.dataset.login) { window.location.href = btn.dataset.login; return; }
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const res = await fetch(btn.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), Accept: "application/json" },
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      btn.classList.toggle("is-liked", data.liked);
      btn.setAttribute("aria-pressed", String(data.liked));
      btn.querySelector(".like-count").textContent = data.count;
    } catch (err) {
      console.error("No se pudo actualizar el me gusta", err);
    } finally {
      btn.disabled = false;
    }
  });

  // ---- 4. Valoraciones con estrellas (AJAX) -----------------------------------
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".rate-btn");
    if (!btn) return;
    const widget = btn.closest(".rating-widget");
    if (widget.dataset.login) { window.location.href = widget.dataset.login; return; }
    if (widget.dataset.own || widget.dataset.busy) return;

    widget.dataset.busy = "1";
    const body = new FormData();
    body.append("score", btn.dataset.score);
    const hint = widget.querySelector(".rating-hint");
    try {
      const res = await fetch(widget.dataset.url, {
        method: "POST", body, credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), Accept: "application/json" },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);

      widget.querySelectorAll(".rate-btn").forEach((b) => {
        const on = data.my_rating && Number(b.dataset.score) <= data.my_rating;
        b.classList.toggle("is-on", Boolean(on));
        b.setAttribute("aria-pressed", String(Number(b.dataset.score) === data.my_rating));
      });
      const text = widget.querySelector(".rating-text");
      text.replaceChildren();
      if (data.count) {
        const strong = document.createElement("strong");
        strong.textContent = data.avg;
        text.append(strong, ` · ${data.count} valoración${data.count === 1 ? "" : "es"}`);
      } else {
        text.textContent = "Sin valoraciones todavía";
      }
      widget.querySelector(".featured-badge").classList.toggle("hidden", !data.featured);
      hint.textContent = data.my_rating
        ? `Tu valoración: ${data.my_rating}/5 (pulsa la misma estrella para quitarla).`
        : "Valoración eliminada.";
    } catch (err) {
      hint.textContent = err.message || "No se pudo guardar.";
    } finally {
      delete widget.dataset.busy;
    }
  });

  // ---- 5. Confirmaciones y protección contra doble envío ----------------------
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    const msg = form.dataset?.confirm;
    if (msg && !window.confirm(msg)) { ev.preventDefault(); return; }
    if (ev.defaultPrevented || form.id === "chat-form") return;
    // setTimeout: así el navegador envía primero el valor del botón pulsado
    setTimeout(() => {
      form.querySelectorAll('button:not([type="button"]), input[type="submit"]').forEach((b) => {
        b.disabled = true;
        b.classList.add("is-busy");
      });
    }, 0);
  });
  window.addEventListener("pageshow", (ev) => {    // volver con "atrás": reactivar botones
    if (!ev.persisted) return;
    document.querySelectorAll("button.is-busy").forEach((b) => { b.disabled = false; b.classList.remove("is-busy"); });
  });

  // ---- 6. Los mensajes flash se cierran al pulsarlos y caducan solos ----------
  document.querySelectorAll(".flash").forEach((el) => {
    el.addEventListener("click", () => el.remove());
    setTimeout(() => el.remove(), 8000);
  });
})();
