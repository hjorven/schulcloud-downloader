"use strict";
(() => {
  const { SchulCloud, Crawler, CorsError, ApiError, planPaths, saveAsZip, saveToFolder } = window.SC;
  const $ = (id) => document.getElementById(id);
  let api = null;
  let items = [];
  const selected = new Set();
  const activeTypes = new Set();
  const openState = new Set();
  let abort = null;
  const canFolder = typeof window.showDirectoryPicker === "function";

  const fmtSize = (n) => {
    if (n == null) return "?";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
  };
  const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("de-DE") : "");
  const ext = (name) => { const m = /\.([^.]+)$/.exec(name); return m ? m[1].toLowerCase() : "(ohne)"; };
  const files = (n) => (n === 1 ? "1 Datei" : n + " Dateien");
  const containerLabel = (it) => it.kind + ": " + it.container;
  const groupLabel = (it) => (it.folders.length ? it.folders.slice(0, 2).join(" / ") : "(ohne Ordner)");

  function show(id) {
    for (const s of ["step-login", "step-scan", "step-select", "step-download"]) $(s).classList.toggle("hidden", s !== id);
  }
  function setMsg(el, text, cls) { el.className = "msg" + (cls ? " " + cls : ""); el.textContent = text; }
  function appendLog(el, line) {
    el.textContent += (el.textContent ? "\n" : "") + line;
    const lines = el.textContent.split("\n");
    if (lines.length > 200) el.textContent = lines.slice(-200).join("\n");
    el.scrollTop = 1e9;
  }

  // ---------------------------------------------------------------- Anmeldung
  async function login() {
    const msg = $("login-msg");
    $("cors-box").classList.add("hidden");
    setMsg(msg, "Anmeldung läuft ...");
    $("btn-login").disabled = true;
    try {
      api = await SchulCloud.login($("url").value.trim(), $("user").value.trim(), $("pw").value);
      $("pw").value = "";
      await scan();
    } catch (e) {
      show("step-login");
      setMsg(msg, e.message, "err");
      if (e instanceof CorsError) $("cors-box").classList.remove("hidden");
    } finally {
      $("btn-login").disabled = false;
    }
  }

  async function scan() {
    show("step-scan");
    $("scan-log").textContent = "";
    const filter = $("filter").value.trim();
    const crawler = new Crawler(api, (m) => appendLog($("scan-log"), m));
    const report = await crawler.run(filter ? [filter] : null);
    items = report.items.map((it, id) => ({ ...it, id }));
    const name = [report.user.firstName, report.user.lastName].filter(Boolean).join(" ");
    $("scan-info").textContent = "Angemeldet als " + name + ". " + files(items.length) + " gefunden.";
    selected.clear();
    items.forEach((it) => selected.add(it.id));
    buildChips();
    render();
    show("step-select");
  }

  // ---------------------------------------------------------------- Auswahl
  function buildChips() {
    const counts = {};
    items.forEach((it) => { const e = ext(it.name); counts[e] = (counts[e] || 0) + 1; });
    const box = $("chips");
    box.textContent = "";
    activeTypes.clear();
    Object.keys(counts).sort().forEach((e) => {
      const c = document.createElement("span");
      c.className = "chip";
      c.textContent = e + " (" + counts[e] + ")";
      c.onclick = () => { activeTypes.has(e) ? activeTypes.delete(e) : activeTypes.add(e); c.classList.toggle("on"); render(); };
      box.appendChild(c);
    });
  }

  function visibleItems() {
    const q = $("search").value.trim().toLowerCase();
    return items.filter((it) => {
      if (activeTypes.size && !activeTypes.has(ext(it.name))) return false;
      if (!q) return true;
      return [containerLabel(it), it.folders.join(" "), it.name].join(" ").toLowerCase().includes(q);
    });
  }

  function checkbox(ids) {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    const n = ids.filter((id) => selected.has(id)).length;
    cb.checked = n === ids.length && n > 0;
    cb.indeterminate = n > 0 && n < ids.length;
    cb.onclick = (ev) => {
      ev.stopPropagation();
      if (cb.checked) ids.forEach((id) => selected.add(id));
      else ids.forEach((id) => selected.delete(id));
      render();
    };
    return cb;
  }

  function summaryRow(el, label, list) {
    const s = document.createElement("summary");
    const caret = document.createElement("span"); caret.className = "caret"; caret.textContent = "▸";
    const name = document.createElement("span"); name.className = "name"; name.textContent = label;
    const meta = document.createElement("span"); meta.className = "meta";
    meta.textContent = files(list.length) + ", " + fmtSize(list.reduce((a, i) => a + (i.size || 0), 0));
    s.append(caret, checkbox(list.map((i) => i.id)), name, meta);
    el.appendChild(s);
  }

  function render() {
    const vis = visibleItems();
    const tree = $("tree");
    tree.textContent = "";
    const byC = new Map();
    for (const it of vis) {
      const c = containerLabel(it);
      if (!byC.has(c)) byC.set(c, new Map());
      const g = byC.get(c);
      const gl = groupLabel(it);
      if (!g.has(gl)) g.set(gl, []);
      g.get(gl).push(it);
    }
    const filtering = $("search").value.trim() !== "" || activeTypes.size > 0;
    for (const [cName, groups] of byC) {
      const d = document.createElement("details");
      d.open = filtering || openState.has(cName);
      d.ontoggle = () => (d.open ? openState.add(cName) : openState.delete(cName));
      summaryRow(d, cName, [...groups.values()].flat());
      for (const [gName, list] of groups) {
        const gd = document.createElement("details");
        gd.className = "lvl2";
        const key = cName + "\u0000" + gName;
        gd.open = filtering || openState.has(key);
        gd.ontoggle = () => (gd.open ? openState.add(key) : openState.delete(key));
        summaryRow(gd, gName, list);
        for (const it of list) {
          const row = document.createElement("label");
          row.className = "file";
          const name = document.createElement("span");
          name.className = "name";
          const sub = it.folders.slice(2).join(" / ");
          name.textContent = (sub ? sub + " / " : "") + it.name;
          name.title = name.textContent;
          const date = document.createElement("span"); date.className = "meta date"; date.textContent = fmtDate(it.updated);
          const size = document.createElement("span"); size.className = "meta"; size.textContent = fmtSize(it.size);
          row.append(checkbox([it.id]), name, date, size);
          gd.appendChild(row);
        }
        d.appendChild(gd);
      }
      tree.appendChild(d);
    }
    if (!vis.length) tree.textContent = items.length ? "Keine Treffer." : "Keine Dateien gefunden.";
    const chosen = items.filter((it) => selected.has(it.id));
    $("sum").textContent = chosen.length + " von " + files(items.length) + " ausgewählt, " +
      fmtSize(chosen.reduce((a, i) => a + (i.size || 0), 0));
    $("btn-download").disabled = chosen.length === 0;
  }

  function updateModeNote() {
    const folder = $("opt-folder").checked;
    $("mode-note").textContent = folder
      ? "Du wählst gleich einen Ordner aus. Dateien, die dort schon mit gleicher Größe liegen, werden übersprungen."
      : "Alles wird im Browser gesammelt und als ZIP gespeichert. Bei sehr vielen Daten entstehen mehrere ZIP-Teile.";
  }

  // ---------------------------------------------------------------- Download
  async function download() {
    const chosen = items.filter((it) => selected.has(it.id));
    const planned = planPaths(chosen);
    const folderMode = $("opt-folder").checked;
    let root = null;
    if (folderMode) {
      try {
        root = await window.showDirectoryPicker({ mode: "readwrite", id: "schulcloud" });
      } catch (e) {
        return; // Auswahl abgebrochen
      }
    }
    abort = new AbortController();
    show("step-download");
    $("btn-back").classList.add("hidden");
    $("btn-cancel").classList.remove("hidden");
    $("dl-log").textContent = "";
    setMsg($("dl-info"), "Lade herunter ...");
    $("bar").max = Math.max(planned.length, 1);
    $("bar").value = 0;
    const onProgress = (done, total, path) => {
      $("bar").value = done;
      setMsg($("dl-info"), done + " von " + total + " Dateien");
      appendLog($("dl-log"), "[" + done + "/" + total + "] " + path);
    };
    const onZip = (blob, part) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "Schulcloud" + (part > 1 ? "-Teil" + part : "") + ".zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    };
    try {
      const r = folderMode
        ? await saveToFolder(api, planned, root, onProgress, abort.signal)
        : await saveAsZip(api, planned, onProgress, abort.signal, onZip);
      let text = "Fertig: " + r.downloaded + " geladen (" + fmtSize(r.bytes) + ")";
      if (folderMode) text += ", " + r.skipped + " schon vorhanden";
      text += ", " + r.failed + " fehlgeschlagen.";
      if (abort.signal.aborted) text = "Abgebrochen. " + text;
      setMsg($("dl-info"), text, r.failed ? "err" : "ok");
      r.errors.slice(0, 50).forEach((e) => appendLog($("dl-log"), "FEHLER " + e));
      if (r.failed && r.errors.some((e) => /erlaubt keinen Download|blockiert/.test(e))) {
        appendLog($("dl-log"), "Tipp: Einige Dateien blockiert die Schul-Cloud für Webseiten. Die lokale Version (sc-dl web) lädt sie.");
      }
    } catch (e) {
      setMsg($("dl-info"), e.message, "err");
    }
    $("btn-cancel").classList.add("hidden");
    $("btn-back").classList.remove("hidden");
  }

  // ---------------------------------------------------------------- Start
  $("login-form").addEventListener("submit", (e) => { e.preventDefault(); login(); });
  $("search").addEventListener("input", render);
  $("btn-all").onclick = () => { visibleItems().forEach((i) => selected.add(i.id)); render(); };
  $("btn-none").onclick = () => { visibleItems().forEach((i) => selected.delete(i.id)); render(); };
  $("btn-download").onclick = download;
  $("btn-cancel").onclick = () => abort && abort.abort();
  $("btn-back").onclick = () => show("step-select");
  $("opt-folder").onchange = updateModeNote;
  $("opt-zip").onchange = updateModeNote;
  if (canFolder) {
    $("opt-folder").checked = true;
  } else {
    $("opt-folder-label").classList.add("hidden");
    $("opt-zip").checked = true;
  }
  updateModeNote();
  try {
    const last = localStorage.getItem("sc-url");
    if (last) $("url").value = last;
    $("url").addEventListener("change", () => { try { localStorage.setItem("sc-url", $("url").value); } catch (e) {} });
  } catch (e) { /* Speicher nicht verfügbar */ }
})();
