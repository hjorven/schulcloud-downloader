// Läuft in Node 18+: sammelt über web/app.js ein und schreibt eine ZIP-Datei.
const fs = require("fs");
const path = require("path");
const SC = require(path.join(__dirname, "..", "web", "app.js"));

(async () => {
  const [base, out] = process.argv.slice(2);
  const api = await SC.SchulCloud.login(base, "a@b.de", "pw");
  api.delay = 0;
  const report = await new SC.Crawler(api).run(null);
  const planned = SC.planPaths(report.items);
  const blobs = [];
  const res = await SC.saveAsZip(api, planned, () => {}, new AbortController().signal, (b) => blobs.push(b));
  fs.writeFileSync(out, Buffer.from(await blobs[0].arrayBuffer()));
  let badLogin = "";
  try { await SC.SchulCloud.login(base, "a@b.de", "falsch"); } catch (e) { badLogin = e.message; }
  console.log(JSON.stringify({ found: report.items.length, warnings: report.warnings, result: res, paths: planned.map((p) => p.path), badLogin }));
})().catch((e) => { console.error(e); process.exit(1); });
