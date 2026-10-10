/* Formulario de posts: bloques de código dinámicos (1 a 5). */
(function () {
  "use strict";
  const container = document.getElementById("code-blocks");
  const template = document.getElementById("code-block-template");
  const addBtn = document.getElementById("add-block");
  if (!container || !template || !addBtn) return;
  const MAX_BLOCKS = 5;

  const rows = () => container.querySelectorAll(".code-block-row");

  function refresh() {
    const all = rows();
    all.forEach((row, i) => {
      row.querySelector(".block-title").textContent = `Bloque ${i + 1}`;
      row.querySelector(".remove-block").classList.toggle("hidden", all.length === 1);
      row.querySelectorAll("[name^='code_blocks-']").forEach((input) => {
        input.name = input.name.replace(/code_blocks-\d+-/, `code_blocks-${i}-`);
      });
    });
    addBtn.classList.toggle("hidden", all.length >= MAX_BLOCKS);
  }

  addBtn.addEventListener("click", () => {
    if (rows().length >= MAX_BLOCKS) return;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = template.innerHTML.replaceAll("__INDEX__", String(rows().length)).trim();
    container.appendChild(wrapper.firstElementChild);
    refresh();
  });

  container.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".remove-block");
    if (!btn) return;
    btn.closest(".code-block-row").remove();
    refresh();
  });

  refresh();
})();
