/* JS global: resaltado, botón "Copiar", likes AJAX y confirmaciones. */
(function () {
  "use strict";

  const csrfToken = () =>
    document.querySelector('meta[name="csrf-token"]')?.content || "";

  // ---- 0. Modo claro / oscuro (el valor inicial lo fija un script en <head>) ----
  const root = document.documentElement;
  const themeBtn = document.getElementById("theme-toggle");
  function paintThemeBtn() {
    if (!themeBtn) return;
    const dark = root.getAttribute("data-theme") === "dark";
    themeBtn.textContent = dark ? "☀️" : "🌙";
    themeBtn.title = dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
    themeBtn.setAttribute("aria-pressed", String(dark));
  }
  if (themeBtn) {
    paintThemeBtn();
    themeBtn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("mypixel-theme", next); } catch (e) { /* modo privado */ }
      paintThemeBtn();
    });
  }

  // ---- 1. Resaltado de sintaxis (highlight.js) ----
  if (window.hljs) {
    document.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));
  }

  // ---- 2. Botón "Copiar código" (delegación de eventos) ----
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // Fallback para http:// o navegadores antiguos
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
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
    try {
      await copyText(code.textContent);
      const original = btn.textContent;
      btn.textContent = "¡COPIADO!";
      btn.classList.add("is-copied");
      setTimeout(() => {
        btn.textContent = original;
        btn.classList.remove("is-copied");
      }, 1800);
    } catch (err) {
      btn.textContent = "ERROR";
    }
  });

  // ---- 3. Me gusta (AJAX) ----
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".like-btn");
    if (!btn) return;
    if (btn.dataset.login) {           // usuario anónimo -> login
      window.location.href = btn.dataset.login;
      return;
    }
    btn.disabled = true;
    try {
      const res = await fetch(btn.dataset.url, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken(), "Accept": "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      btn.classList.toggle("is-liked", data.liked);
      btn.setAttribute("aria-pressed", String(data.liked));
      btn.querySelector(".like-count").textContent = data.count;
    } catch (err) {
      console.error("No se pudo actualizar el like", err);
    } finally {
      btn.disabled = false;
    }
  });


  // ---- 3b. Valoraciones con estrellas (AJAX) ----
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".rate-btn");
    if (!btn) return;
    const widget = btn.closest(".rating-widget");
    if (widget.dataset.login) { window.location.href = widget.dataset.login; return; }
    if (widget.dataset.own) return;

    const body = new FormData();
    body.append("score", btn.dataset.score);
    try {
      const res = await fetch(widget.dataset.url, {
        method: "POST", body, credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), "Accept": "application/json" },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);

      widget.querySelectorAll(".rate-btn").forEach((b) => {
        const on = data.my_rating && Number(b.dataset.score) <= data.my_rating;
        b.classList.toggle("is-on", Boolean(on));
        b.setAttribute("aria-pressed", String(Number(b.dataset.score) === data.my_rating));
      });
      widget.querySelector(".rating-text").innerHTML = data.count
        ? `<b>${data.avg}</b> · ${data.count} valoración${data.count === 1 ? "" : "es"}`
        : "Sin valoraciones todavía";
      widget.querySelector(".featured-badge").classList.toggle("hidden", !data.featured);
      widget.querySelector(".rating-hint").textContent = data.my_rating
        ? `Tu valoración: ${data.my_rating}★ (pulsa la misma estrella para quitarla).`
        : "Valoración eliminada.";
    } catch (err) {
      widget.querySelector(".rating-hint").textContent = err.message || "No se pudo guardar.";
    }
  });

  // ---- 4. Confirmación en formularios destructivos ----
  document.addEventListener("submit", (ev) => {
    const msg = ev.target.dataset?.confirm;
    if (msg && !window.confirm(msg)) ev.preventDefault();
  });

  // ---- 5. Menú de cuenta (desplegable de la barra superior) ----
  const acct = document.querySelector(".acct-menu");
  if (acct) {
    const btn = acct.querySelector(".acct-btn");
    const setOpen = (open) => { acct.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open)); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!acct.classList.contains("open")); });
    document.addEventListener("click", (e) => { if (!acct.contains(e.target)) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }
})();
