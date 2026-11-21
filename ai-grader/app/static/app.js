document.addEventListener("submit", (e) => {
  const msg = e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

const MSG = document.getElementById("drop-messages")?.dataset || {};

document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
  const root = document.documentElement;
  const sync = () => btn.setAttribute("aria-checked", String(root.dataset.theme === "dark"));
  sync();
  btn.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
    sync();
  });
});

document.addEventListener("click", (e) => {
  document.querySelectorAll("details[data-menu][open]").forEach((menu) => {
    if (!menu.contains(e.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll("details[data-menu][open]").forEach((menu) => { menu.open = false; });
});

document.querySelectorAll("form[data-autosave]").forEach((form) => {
  form.addEventListener("change", () => form.submit());
});

document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const el = document.querySelector(btn.dataset.copy);
    try { await navigator.clipboard.writeText(el.value || el.textContent); }
    catch (e) { el.select(); document.execCommand("copy"); }
    const old = btn.textContent;
    btn.textContent = btn.dataset.copied || "OK";
    setTimeout(() => { btn.textContent = old; }, 1500);
  });
});

["dragover", "drop"].forEach((type) =>
  window.addEventListener(type, (e) => {
    if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault();
  })
);

function hasFiles(e) {
  return e.dataTransfer && [...e.dataTransfer.types].includes("Files");
}

function bindDragHighlight(el) {
  let depth = 0;
  el.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth++; el.classList.add("drag-over"); } });
  el.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) el.classList.remove("drag-over"); });
  el.addEventListener("drop", () => { depth = 0; el.classList.remove("drag-over"); });
  el.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
}

async function fillTextareaFromFile(textarea, file) {
  const original = textarea.placeholder;
  textarea.placeholder = MSG.reading || "…";
  textarea.classList.add("busy");
  try {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/extract-text", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    textarea.value = textarea.value.trim() ? textarea.value.trimEnd() + "\n" + data.text : data.text;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  } catch (err) {
    alert(`${MSG.error || "Error"}: ${file.name}\n${err.message}`);
  } finally {
    textarea.placeholder = original;
    textarea.classList.remove("busy");
  }
}

function enableTextareaDrop(textarea) {
  if (textarea.dataset.dropBound) return;
  textarea.dataset.dropBound = "1";
  bindDragHighlight(textarea);
  textarea.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    for (const file of e.dataTransfer.files) await fillTextareaFromFile(textarea, file);
  });
}

document.querySelectorAll("textarea[data-drop]").forEach(enableTextareaDrop);

const UPLOADABLE = /\.(docx|odt|rtf|pdf|txt|md|html?|zip|rar|7z|tar|tgz|tar\.gz|tbz2|tar\.bz2|txz|tar\.xz|eml|msg)$/i;
const JUNK = /(^|\/)(\.|~\$|__MACOSX\/|thumbs\.db$|desktop\.ini$)/i;
const keep = (path) => UPLOADABLE.test(path) && !JUNK.test(path);

function readAllEntries(dirEntry) {
  const reader = dirEntry.createReader();
  const all = [];
  return new Promise((resolve, reject) => {
    const next = () => reader.readEntries((batch) => {
      if (!batch.length) return resolve(all);
      all.push(...batch);
      next();
    }, reject);
    next();
  });
}

async function walkEntry(entry, prefix, out) {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej));
    out.push({ file, path: prefix + file.name });
  } else if (entry.isDirectory) {
    for (const child of await readAllEntries(entry)) {
      await walkEntry(child, prefix + entry.name + "/", out);
    }
  }
}

async function filesFromDrop(dataTransfer) {
  const entries = [...dataTransfer.items]
    .filter((it) => it.kind === "file")
    .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null));
  if (!entries.length || entries.some((e) => !e)) {
    return [...dataTransfer.files].map((file) => ({ file, path: file.name }));
  }
  const out = [];
  for (const entry of entries) await walkEntry(entry, "", out);
  return out;
}

function filesFromInput(input) {
  return [...input.files].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

async function uploadItems(form, zone, items) {
  items = items.filter((i) => keep(i.path));
  if (!items.length) return;
  zone.classList.add("busy");
  const title = zone.querySelector(".dz-title");
  if (title) title.textContent = `${MSG.uploading || "…"} (${items.length})`;
  const body = new FormData();
  for (const { file, path } of items) {
    body.append("files", file, file.name);
    body.append("paths", path);
  }
  const auto = form.querySelector("input[name=auto_grade]");
  if (auto && auto.checked) body.append("auto_grade", "1");
  try {
    const res = await fetch(form.action, { method: "POST", body });
    window.location = res.url;
  } catch (err) {
    zone.classList.remove("busy");
    alert(`${MSG.error || "Error"}: ${err.message}`);
  }
}

document.querySelectorAll("form.upload-form").forEach((form) => {
  const zone = form.querySelector(".dropzone");
  const fileInput = zone.querySelector("input[type=file]");
  bindDragHighlight(zone);
  zone.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    uploadItems(form, zone, await filesFromDrop(e.dataTransfer));
  });
  fileInput.addEventListener("change", () => uploadItems(form, zone, filesFromInput(fileInput)));
  const folderInput = form.querySelector("[data-folder-input]");
  folderInput?.addEventListener("change", () => uploadItems(form, zone, filesFromInput(folderInput)));
});

const criteria = document.getElementById("criteria");
const template = document.getElementById("criterion-template");
const addBtn = document.getElementById("add-criterion");
if (criteria && template && addBtn) {
  addBtn.addEventListener("click", () => {
    criteria.appendChild(template.content.cloneNode(true));
    const row = criteria.lastElementChild;
    row.querySelectorAll("textarea[data-drop]").forEach(enableTextareaDrop);
    row.querySelector("input[name=crit_name]").focus();
  });
  criteria.addEventListener("click", (e) => {
    if (!e.target.matches("[data-remove-row]")) return;
    if (criteria.querySelectorAll(".criterion-row").length > 1) {
      e.target.closest(".criterion-row").remove();
    }
  });
}

const total = document.querySelector(".total b");
const pointInputs = document.querySelectorAll("input[name^=points_]");
if (total && pointInputs.length) {
  const decimalComma = document.documentElement.lang !== "en";
  const update = () => {
    let sum = 0;
    pointInputs.forEach((i) => { sum += parseFloat(String(i.value).replace(",", ".")) || 0; });
    const text = String(Math.round(sum * 10) / 10);
    total.textContent = decimalComma ? text.replace(".", ",") : text;
  };
  pointInputs.forEach((i) => i.addEventListener("input", update));
}

const progressBox = document.querySelector("[data-progress-url]");
if (progressBox) {
  const d = progressBox.dataset;
  const countEl = progressBox.querySelector("[data-p-count]");
  const etaEl = progressBox.querySelector("[data-p-eta]");
  const errEl = progressBox.querySelector("[data-p-errors]");
  const fill = progressBox.querySelector(".fill");
  const baseTitle = document.title;

  const fmtTime = (sec) => (sec < 60 ? `${Math.max(5, Math.round(sec / 5) * 5)} s` : `${Math.round(sec / 60)} min`);

  const updateRows = (rows) => {
    for (const r of rows) {
      const tr = document.querySelector(`tr[data-sub-id="${r.id}"]`);
      if (!tr) continue;
      const badge = tr.querySelector("[data-c-status] .badge");
      if (badge && !badge.classList.contains(r.status)) {
        badge.className = `badge ${r.status}`;
        badge.textContent = r.label;
        tr.classList.add("just-updated");
      }
      const flag = tr.querySelector("[data-c-status] [data-flag-injection]");
      if (flag) flag.hidden = !r.flag;
      const unsure = tr.querySelector("[data-c-status] [data-flag-unsure]");
      if (unsure) unsure.hidden = !r.unsure;
      const ai = tr.querySelector("[data-c-ai]");
      if (ai) ai.textContent = r.ai;
    }
  };

  const poll = async () => {
    try {
      const res = await fetch(d.progressUrl, { cache: "no-store" });
      const p = await res.json();
      fill.style.width = `${p.percent}%`;
      countEl.textContent = `${p.done} / ${p.total || "…"} (${p.percent}%)`;
      document.title = `(${p.percent}%) ${baseTitle}`;
      errEl.textContent = p.errors ? "· " + d.errors.replace("{n}", p.errors) : "";
      updateRows(p.rows);
      if (!p.running) {
        etaEl.textContent = "· " + d.done;
        fill.style.width = "100%";
        setTimeout(() => window.location.reload(), 1200);
        return;
      }
      etaEl.textContent = "· " + (p.eta_seconds == null ? d.calc : d.eta.replace("{t}", fmtTime(p.eta_seconds)));
    } catch (e) {}
    setTimeout(poll, 2000);
  };
  poll();
}
