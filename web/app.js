// Schul-Cloud Massendownload im Browser (für GitHub Pages).
// Alle Anfragen gehen direkt vom Browser an die Schul-Cloud. Es gibt keinen eigenen Server.
"use strict";

const PAGE_SIZE = 50;
const RETRY = new Set([429, 500, 502, 503, 504]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class ApiError extends Error {
  constructor(status, message, path) {
    super(message);
    this.status = status;
    this.path = path;
  }
}

class CorsError extends Error {}

// ------------------------------------------------------------------ API-Client
class SchulCloud {
  constructor(baseUrl, token, delay = 80) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.token = token;
    this.delay = delay;
  }

  static async login(baseUrl, username, password) {
    const base = baseUrl.replace(/\/+$/, "");
    let res;
    try {
      res = await fetch(base + "/api/v3/authentication/local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
    } catch (e) {
      throw new CorsError(
        "Die Schul-Cloud hat die Anfrage von dieser Seite blockiert oder ist nicht erreichbar."
      );
    }
    if (res.status === 401 || res.status === 403) throw new ApiError(res.status, "E-Mail oder Passwort falsch.");
    if (!res.ok) throw new ApiError(res.status, "Anmeldung fehlgeschlagen (HTTP " + res.status + ").");
    const data = await res.json();
    if (!data.accessToken) throw new ApiError(res.status, "Antwort enthält kein Token.");
    return new SchulCloud(base, data.accessToken);
  }

  url(path) {
    return /^https?:\/\//.test(path) ? path : this.base + path;
  }

  async request(path, params) {
    let url = this.url(path);
    if (params) {
      const qs = new URLSearchParams();
      for (const [k, v] of params) qs.append(k, v);
      url += (url.includes("?") ? "&" : "?") + qs.toString();
    }
    for (let attempt = 1; ; attempt++) {
      if (this.delay) await sleep(this.delay);
      let res;
      try {
        res = await fetch(url, { headers: { Authorization: "Bearer " + this.token } });
      } catch (e) {
        if (attempt > 3) throw new CorsError("Netzwerkfehler oder von der Schul-Cloud blockiert: " + path);
        await sleep(1000 * 2 ** attempt);
        continue;
      }
      if (RETRY.has(res.status) && attempt <= 3) {
        await sleep(1000 * 2 ** attempt);
        continue;
      }
      if (res.status === 401) throw new ApiError(401, "Anmeldung abgelaufen. Bitte neu anmelden.", path);
      if (!res.ok) throw new ApiError(res.status, "HTTP " + res.status, path);
      return res;
    }
  }

  async json(path, params) {
    return (await this.request(path, params)).json();
  }

  async *paginate(path) {
    let skip = 0;
    for (;;) {
      const page = await this.json(path, [["skip", skip], ["limit", PAGE_SIZE]]);
      const data = page.data || [];
      yield* data;
      skip += data.length;
      if (!data.length || (page.total != null && skip >= page.total)) break;
    }
  }

  async all(path) {
    const out = [];
    for await (const x of this.paginate(path)) out.push(x);
    return out;
  }

  me() { return this.json("/api/v3/me"); }
  courses() { return this.all("/api/v3/courses"); }
  courseBoard(id) { return this.json(`/api/v3/course-rooms/${id}/board`); }
  lesson(id) { return this.json(`/api/v3/lessons/${id}`); }
  async rooms() { return (await this.json("/api/v3/rooms")).data || []; }
  async roomBoards(id) { return (await this.json(`/api/v3/rooms/${id}/boards`)).data || []; }
  board(id) { return this.json(`/api/v3/boards/${id}`); }
  async cards(ids) {
    const out = [];
    for (let i = 0; i < ids.length; i += 20) {
      const page = await this.json("/api/v3/cards", ids.slice(i, i + 20).map((id) => ["ids", id]));
      out.push(...(page.data || []));
    }
    return out;
  }
  listFiles(schoolId, parentType, parentId) {
    return this.all(`/api/v3/file/list/school/${schoolId}/${parentType}/${parentId}`);
  }
  async legacyFiles(ownerId, parentId) {
    const params = [["owner", ownerId]];
    if (parentId) params.push(["parent", parentId]);
    const data = await this.json("/api/v1/fileStorage", params);
    return Array.isArray(data) ? data : data.data || [];
  }
  async legacySignedUrl(fileId) {
    const data = await this.json("/api/v1/fileStorage/signedUrl", [["file", fileId], ["download", "true"]]);
    if (!data.url) throw new ApiError(200, "Keine Download-URL");
    return data.url;
  }

  // Liefert die Datei als Response (Stream). Der Token geht nur an die Schul-Cloud selbst.
  async fetchFile(item) {
    if (item.source === "legacy") {
      const signed = await this.legacySignedUrl(item.ref);
      let res;
      try {
        res = await fetch(signed);
      } catch (e) {
        throw new CorsError("Der Dateispeicher erlaubt keinen Download von dieser Seite.");
      }
      if (!res.ok) throw new ApiError(res.status, "HTTP " + res.status);
      return res;
    }
    return this.request(item.ref);
  }
}

// --------------------------------------------------------------------- Crawler
const LEGACY_LINK = /file=([0-9a-fA-F]{24})/g;

function matchesAny(name, needles) {
  if (!needles || !needles.length) return true;
  const low = (name || "").toLowerCase();
  return needles.some((n) => low.includes(n.toLowerCase()));
}

class Crawler {
  constructor(api, log) {
    this.api = api;
    this.log = log || (() => {});
    this.items = [];
    this.warnings = [];
    this.schoolId = "";
  }

  warn(msg) {
    this.warnings.push(msg);
    this.log("Hinweis: " + msg);
  }

  async safe(what, fn, fallback) {
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) throw e;
      if (e instanceof CorsError) throw e;
      if (!(e instanceof ApiError) || ![403, 404].includes(e.status)) this.warn(what + ": " + e.message);
      return fallback;
    }
  }

  async v3Files(schoolId, parentType, parentId, kind, container, folders, topic) {
    const records = await this.safe(
      `Dateien ${parentType}`,
      () => this.api.listFiles(schoolId, parentType, parentId),
      []
    );
    for (const r of records) {
      if (r.deletedSince || r.isUploading) continue;
      if (r.securityCheckStatus === "blocked") {
        this.warn("Vom Virenscanner blockiert: " + r.name);
        continue;
      }
      this.items.push({
        kind, container, folders: [...folders], topic,
        name: r.name || r.id, size: r.size ?? null, mime: r.mimeType || null,
        created: r.createdAt || null, updated: r.contentLastModifiedAt || r.updatedAt || null,
        source: "v3", ref: r.url || `/api/v3/file/download/${r.id}/${encodeURIComponent(r.name)}`, fileId: r.id,
      });
    }
  }

  async run(filter) {
    const me = await this.api.me();
    this.schoolId = (me.school || {}).id || "";
    for (const c of await this.api.courses()) {
      if (!matchesAny(c.title, filter)) continue;
      if (c.isLocked) { this.warn("Kurs gesperrt: " + c.title); continue; }
      await this.course(c);
    }
    const rooms = await this.safe("Räume", () => this.api.rooms(), []);
    for (const r of rooms) {
      if (!matchesAny(r.name, filter)) continue;
      await this.room(r);
    }
    return { items: this.items, warnings: this.warnings, user: me.user || {}, school: me.school || {} };
  }

  async course(course) {
    const name = course.title || course.id;
    this.log("Kurs: " + name);
    const legacyById = new Map();
    await this.legacyTree(course.id, name, ["Kursdateien"], null, legacyById, 0);
    const board = await this.safe("Kurs " + name, () => this.api.courseBoard(course.id), {});
    let lessonNo = 0;
    for (const el of board.elements || []) {
      const content = el.content || {};
      if (el.type === "lesson") {
        lessonNo++;
        await this.lesson(content, name, lessonNo, legacyById);
      } else if (el.type === "task") {
        const title = content.name || content.id;
        await this.v3Files(this.schoolId, "tasks", content.id, "Kurs", name, ["Aufgaben", title], title);
      } else if (el.type === "column-board") {
        await this.board(content.columnBoardId, "Kurs", name, this.schoolId, "Board - " + (content.title || "Board"));
      }
    }
  }

  async lesson(content, courseName, no, legacyById) {
    const title = content.name || content.id;
    const folders = ["Themen", String(no).padStart(2, "0") + " " + title];
    await this.v3Files(this.schoolId, "lessons", content.id, "Kurs", courseName, folders, title);
    const lesson = await this.safe("Thema " + title, () => this.api.lesson(content.id), {});
    const seen = new Set();
    for (const part of lesson.contents || []) {
      const text = String((part.content || {}).text || "");
      for (const m of text.matchAll(LEGACY_LINK)) {
        const id = m[1];
        if (seen.has(id) || !legacyById.has(id)) continue;
        seen.add(id);
        this.items.push({ ...legacyById.get(id), folders: [...folders], topic: title });
      }
    }
  }

  async legacyTree(ownerId, courseName, folders, parentId, index, depth) {
    if (depth > 20) return;
    const entries = await this.safe("Kursdateien " + courseName, () => this.api.legacyFiles(ownerId, parentId), []);
    for (const e of entries) {
      const id = String(e._id || e.id);
      if (e.isDirectory) {
        await this.legacyTree(ownerId, courseName, [...folders, e.name || id], id, index, depth + 1);
        continue;
      }
      const item = {
        kind: "Kurs", container: courseName, folders: [...folders], topic: folders.slice(1).join("/"),
        name: e.name || id, size: e.size ?? null, mime: e.type || null,
        created: e.createdAt || null, updated: e.updatedAt || null, source: "legacy", ref: id, fileId: id,
      };
      index.set(id, item);
      this.items.push(item);
    }
  }

  async room(room) {
    const name = room.name || room.id;
    this.log("Raum: " + name);
    const schoolId = room.schoolId || this.schoolId;
    for (const b of await this.safe("Raum " + name, () => this.api.roomBoards(room.id), [])) {
      await this.board(b.id, "Raum", name, schoolId, "Board - " + (b.title || "Board"));
    }
  }

  async board(boardId, kind, container, schoolId, boardFolder) {
    const board = await this.safe(boardFolder, () => this.api.board(boardId), {});
    const cols = board.columns || [];
    for (let ci = 0; ci < cols.length; ci++) {
      const col = cols[ci];
      const colTitle = col.title || "Abschnitt " + (ci + 1);
      const ids = (col.cards || []).map((c) => c.cardId).filter(Boolean);
      if (!ids.length) continue;
      const cards = await this.safe("Karten", () => this.api.cards(ids), []);
      cards.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
      for (let k = 0; k < cards.length; k++) {
        const card = cards[k];
        const cardTitle = card.title || "Karte " + (k + 1);
        const base = [boardFolder, pad(ci + 1) + " " + colTitle, pad(k + 1) + " " + cardTitle];
        const topic = boardFolder.replace(/^Board - /, "") + " / " + colTitle + " / " + cardTitle;
        for (const el of card.elements || []) {
          if (el.type === "file") {
            await this.v3Files(schoolId, "boardnodes", el.id, kind, container, base, topic);
          } else if (el.type === "fileFolder") {
            const sub = (el.content || {}).title || el.title || "Ordner";
            await this.v3Files(schoolId, "boardnodes", el.id, kind, container, [...base, sub], topic);
          }
        }
      }
    }
  }
}

function pad(n) { return String(n).padStart(2, "0"); }

// ---------------------------------------------------------- Pfade und Namen
const INVALID = /[<>:"/\\|?*\x00-\x1f]/g;
const RESERVED = new Set(["CON", "PRN", "AUX", "NUL", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => ["COM" + i, "LPT" + i])]);

function safeName(name, maxLen = 120) {
  let s = String(name).replace(INVALID, "_").trim().replace(/\.+$/, "").replace(/\s+/g, " ");
  if (!s) s = "_";
  if (RESERVED.has(s.split(".")[0].toUpperCase())) s = "_" + s;
  if (s.length > maxLen) {
    const dot = s.lastIndexOf(".");
    const ext = dot > 0 ? s.slice(dot + 1) : "";
    s = ext && ext.length <= 10 ? s.slice(0, maxLen - ext.length - 1).trimEnd() + "." + ext : s.slice(0, maxLen).trimEnd();
  }
  return s;
}

function planPaths(items) {
  const used = new Set();
  return items.map((it) => {
    const parts = [safeName((it.kind === "Raum" ? "Raum " : "") + it.container), ...it.folders.map((f) => safeName(f))];
    const name = safeName(it.name);
    let candidate = [...parts, name].join("/");
    for (let n = 2; used.has(candidate.toLowerCase()); n++) {
      const dot = name.lastIndexOf(".");
      const alt = dot > 0 ? `${name.slice(0, dot)} (${n})${name.slice(dot)}` : `${name} (${n})`;
      candidate = [...parts, alt].join("/");
    }
    used.add(candidate.toLowerCase());
    return { item: it, path: candidate };
  });
}

// --------------------------------------------------------------- ZIP (Store)
// Unkomprimiertes ZIP. Reicht für PDFs/Bilder, die ohnehin schon komprimiert sind.
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes, crc = 0) {
  crc = ~crc >>> 0;
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return ~crc >>> 0;
}

function dosTime(d) {
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const date = ((Math.max(d.getFullYear(), 1980) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  return { time, date };
}

class ZipWriter {
  constructor() {
    this.parts = [];
    this.central = [];
    this.offset = 0;
  }

  add(path, bytes, mtime) {
    const name = new TextEncoder().encode(path);
    const crc = crc32(bytes);
    const { time, date } = dosTime(mtime || new Date());
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true);
    h.setUint16(4, 20, true);
    h.setUint16(6, 0x0800, true); // UTF-8-Dateinamen
    h.setUint16(8, 0, true); // Store
    h.setUint16(10, time, true);
    h.setUint16(12, date, true);
    h.setUint32(14, crc, true);
    h.setUint32(18, bytes.length, true);
    h.setUint32(22, bytes.length, true);
    h.setUint16(26, name.length, true);
    h.setUint16(28, 0, true);
    this.parts.push(h.buffer, name, bytes);

    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true);
    c.setUint16(4, 20, true);
    c.setUint16(6, 20, true);
    c.setUint16(8, 0x0800, true);
    c.setUint16(10, 0, true);
    c.setUint16(12, time, true);
    c.setUint16(14, date, true);
    c.setUint32(16, crc, true);
    c.setUint32(20, bytes.length, true);
    c.setUint32(24, bytes.length, true);
    c.setUint16(28, name.length, true);
    c.setUint32(42, this.offset, true);
    this.central.push(c.buffer, name);
    this.offset += 30 + name.length + bytes.length;
  }

  get size() { return this.offset; }

  finish() {
    const cdSize = this.central.reduce((a, p) => a + p.byteLength, 0);
    const count = this.central.length / 2;
    const e = new DataView(new ArrayBuffer(22));
    e.setUint32(0, 0x06054b50, true);
    e.setUint16(8, count, true);
    e.setUint16(10, count, true);
    e.setUint32(12, cdSize, true);
    e.setUint32(16, this.offset, true);
    return new Blob([...this.parts, ...this.central, e.buffer], { type: "application/zip" });
  }
}

// ------------------------------------------------------------------ Speichern
async function readAll(res) {
  return new Uint8Array(await res.arrayBuffer());
}

async function getSubdir(root, parts) {
  let dir = root;
  for (const p of parts) dir = await dir.getDirectoryHandle(p, { create: true });
  return dir;
}

// Speichert direkt in einen Ordner (Chrome/Edge). Vorhandene Dateien mit gleicher Größe werden übersprungen.
async function saveToFolder(api, planned, root, onProgress, signal) {
  const result = { downloaded: 0, skipped: 0, failed: 0, bytes: 0, errors: [] };
  let done = 0;
  const queue = [...planned];
  async function worker() {
    while (queue.length && !signal.aborted) {
      const { item, path } = queue.shift();
      const parts = path.split("/");
      const fileName = parts.pop();
      try {
        const dir = await getSubdir(root, parts);
        let existing = null;
        try { existing = await (await dir.getFileHandle(fileName)).getFile(); } catch (e) { /* neu */ }
        if (existing && item.size != null && existing.size === item.size) {
          result.skipped++;
        } else {
          const res = await api.fetchFile(item);
          const handle = await dir.getFileHandle(fileName, { create: true });
          const writable = await handle.createWritable();
          if (res.body) await res.body.pipeTo(writable);
          else { await writable.write(await res.blob()); await writable.close(); }
          result.downloaded++;
          result.bytes += item.size || 0;
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) throw e;
        result.failed++;
        result.errors.push(path + ": " + e.message);
      }
      done++;
      onProgress(done, planned.length, path);
    }
  }
  await Promise.all([worker(), worker(), worker()]);
  return result;
}

// ZIP-Dateien zum Herunterladen. Bei sehr großen Mengen in mehrere Teile (je max. ca. 1,5 GB).
async function saveAsZip(api, planned, onProgress, signal, onZip, maxPart = 1.5 * 1024 ** 3) {
  const result = { downloaded: 0, skipped: 0, failed: 0, bytes: 0, errors: [] };
  let zip = new ZipWriter();
  let part = 1;
  let done = 0;
  for (const { item, path } of planned) {
    if (signal.aborted) break;
    try {
      const bytes = await readAll(await api.fetchFile(item));
      if (zip.size > 0 && zip.size + bytes.length > maxPart) {
        onZip(zip.finish(), part++);
        zip = new ZipWriter();
      }
      zip.add(path, bytes, item.updated ? new Date(item.updated) : null);
      result.downloaded++;
      result.bytes += bytes.length;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) throw e;
      result.failed++;
      result.errors.push(path + ": " + e.message);
    }
    done++;
    onProgress(done, planned.length, path);
  }
  if (zip.size > 0) onZip(zip.finish(), part);
  return result;
}

// Für Tests in Node exportieren, im Browser global machen.
const SC = { SchulCloud, Crawler, ApiError, CorsError, safeName, planPaths, ZipWriter, crc32, saveAsZip, saveToFolder };
if (typeof module !== "undefined") module.exports = SC;
else window.SC = SC;
