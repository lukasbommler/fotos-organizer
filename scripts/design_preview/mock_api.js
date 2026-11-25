// Stand-in for web/js/api.js in the design previews: an invented album with painted photos.
// Nothing is read from a disk, nothing is moved, nothing is sent anywhere.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const ALBUM = { id: "demo", name: "Südtirol 2025", path: "C:\\Beispiel\\Bilder\\Südtirol 2025" };
const DAY0 = Date.UTC(2025, 7, 4, 8, 30) / 1000;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// ── painted photos ──────────────────────────────────────────────────────────────────────

function rng(seed) {
  let s = (seed * 2654435761) % 4294967296;
  return () => ((s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296);
}

const MOTIFS = ["lake", "peaks", "sunset", "meadow", "village", "night", "beach", "forest"];
const SKIES = {
  lake: ["#2f74b8", "#cfe6f2"], peaks: ["#3b6fb0", "#dfe9ef"], sunset: ["#3c2a5e", "#ff9a5a"], meadow: ["#4d93cf", "#e9f3ea"],
  village: ["#6d93b5", "#f5dcb8"], night: ["#070b1f", "#2a3a6b"], beach: ["#2b8ac6", "#d9f1f4"], forest: ["#7ea3b4", "#dfe8e2"],
};
const GROUND = {
  lake: ["#5f7f94", "#3f6b62", "#1f4a52"], peaks: ["#9aa9b8", "#6c7f92", "#3d4f5c"], sunset: ["#6b3f63", "#432a4b", "#1d1428"],
  meadow: ["#7fa37a", "#5f8f4c", "#3c6e31"], village: ["#8b8f8a", "#6a7a5c", "#4b5d3e"], night: ["#1d2747", "#131b35", "#0a0f20"],
  beach: ["#8fb7bf", "#2e93a8", "#e4cf9f"], forest: ["#7d9a92", "#466c5b", "#22463a"],
};

/** One holiday picture: `motif` and `seed` give the scene, `shift` the small change within a series. */
function paint({ motif, seed, shift = 0, dark = 0, blur = 0 }, w, hgt) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = hgt;
  const g = c.getContext("2d");
  const r = rng(seed + 11);
  if (blur) g.filter = `blur(${(blur * w) / 480}px)`;
  const sky = g.createLinearGradient(0, 0, 0, hgt * 0.75);
  sky.addColorStop(0, SKIES[motif][0]);
  sky.addColorStop(1, SKIES[motif][1]);
  g.fillStyle = sky;
  g.fillRect(-9, -9, w + 18, hgt + 18);
  const dx = shift * w * 0.03;
  const sunX = w * (0.2 + r() * 0.6) + dx * 1.4;
  const sunY = hgt * (motif === "sunset" ? 0.5 : 0.16 + r() * 0.16);
  const sunR = w * (motif === "sunset" ? 0.085 : 0.045);
  const halo = g.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR * 5);
  halo.addColorStop(0, motif === "night" ? "rgba(220,230,255,.8)" : "rgba(255,248,220,.95)");
  halo.addColorStop(0.2, motif === "night" ? "rgba(200,215,255,.35)" : "rgba(255,240,200,.55)");
  halo.addColorStop(1, "rgba(255,240,200,0)");
  g.fillStyle = halo;
  g.fillRect(0, 0, w, hgt);
  if (motif === "night") {
    g.fillStyle = "#fff";
    for (let i = 0; i < 70; i++) { g.globalAlpha = r(); g.fillRect(r() * w, r() * hgt * 0.55, w / 380, w / 380); }
    g.globalAlpha = 1;
  } else {
    g.fillStyle = "rgba(255,255,255,.5)";
    for (let i = 0; i < 5; i++) {
      const x = r() * w + dx * 2;
      const y = hgt * (0.08 + r() * 0.26);
      for (let k = 0; k < 5; k++) { g.beginPath(); g.ellipse(x + (k - 2) * w * 0.035, y + r() * 6, w * 0.045, hgt * 0.018, 0, 0, 7); g.fill(); }
    }
  }
  const jag = motif === "peaks" || motif === "lake" ? 1 : 0.35;
  for (let layer = 0; layer < 3; layer++) {
    const base = hgt * (0.5 + layer * 0.13);
    const amp = hgt * (0.2 - layer * 0.055) * (0.5 + jag * 0.6);
    const ph = r() * 9;
    const par = dx * (1 + layer * 0.9);
    g.fillStyle = GROUND[motif][layer];
    g.beginPath();
    g.moveTo(-9, hgt + 9);
    for (let i = 0; i <= 60; i++) {
      const u = i / 60 + par / w;
      const big = Math.sin(u * 6.3 * (1 + layer * 0.5) + ph) * 0.5 + Math.sin(u * 15 + ph * 2.1) * 0.28;
      const fine = jag * (Math.abs(Math.sin(u * 31 + ph * 3)) * 0.35 + Math.abs(Math.sin(u * 67 + ph)) * 0.16);
      g.lineTo((i / 60) * (w + 18) - 9, base - amp * (big + fine));
    }
    g.lineTo(w + 9, hgt + 9);
    g.fill();
  }
  if (motif === "lake" || motif === "beach") {
    const y = hgt * (motif === "lake" ? 0.72 : 0.66);
    const water = g.createLinearGradient(0, y, 0, hgt);
    water.addColorStop(0, motif === "lake" ? "#3f8f9c" : "#5cc6cf");
    water.addColorStop(1, motif === "lake" ? "#123f52" : "#1f7f9a");
    g.fillStyle = water;
    g.fillRect(-9, y, w + 18, hgt * (motif === "beach" ? 0.17 : 0.4));
    g.fillStyle = "rgba(255,255,255,.25)";
    for (let i = 0; i < 46; i++) g.fillRect(r() * w + dx, y + r() * hgt * 0.16, w * (0.01 + r() * 0.05), hgt / 320);
    if (motif === "beach") { g.fillStyle = "#e6d2a4"; g.fillRect(-9, hgt * 0.83, w + 18, hgt); }
  }
  if (motif === "forest" || motif === "meadow") {
    for (let i = 0; i < 24; i++) {
      const x = r() * w + dx * 2.6;
      const y = hgt * (0.74 + r() * 0.24);
      const s = hgt * (0.06 + r() * 0.12) * (motif === "forest" ? 1.3 : 0.6);
      g.fillStyle = `rgba(${20 + r() * 25},${50 + r() * 40},${35 + r() * 20},.95)`;
      g.beginPath();
      g.moveTo(x, y - s * 1.8);
      g.lineTo(x + s * 0.45, y);
      g.lineTo(x - s * 0.45, y);
      g.fill();
    }
  }
  if (motif === "village") {
    for (let i = 0; i < 11; i++) {
      const x = w * (0.08 + i * 0.082) + dx * 2;
      const y = hgt * (0.7 + r() * 0.09);
      const bw = w * (0.05 + r() * 0.03);
      const bh = hgt * (0.1 + r() * 0.1);
      g.fillStyle = ["#e8dcc8", "#d9c4a4", "#f1e7d6", "#c9b28f"][i % 4];
      g.fillRect(x, y, bw, bh + hgt * 0.2);
      g.fillStyle = "#8a4a36";
      g.beginPath();
      g.moveTo(x - 4, y);
      g.lineTo(x + bw / 2, y - bh * 0.42);
      g.lineTo(x + bw + 4, y);
      g.fill();
    }
  }
  if (dark) { g.filter = "none"; g.fillStyle = `rgba(0,0,0,${dark})`; g.fillRect(0, 0, w, hgt); }
  return c;
}

// ── the album ───────────────────────────────────────────────────────────────────────────

const photos = [];
const groups = [];
const facts = {};
const shared = { mode: "clean", keep_target: 0.5, level: 2, low_singles: false };
const manual = new Map();
const done = new Set();
let moved = null; // {at, ids} after "Übernehmen"
let hold = false;
let job = null;

(function build() {
  const add = (scene, t, extra = {}) => {
    const id = photos.length + 1;
    photos.push({ id, n: id, name: `IMG_${4100 + id}.jpg`, rel_path: `IMG_${4100 + id}.jpg`, w: 6000, h: 4000, size: 9e6 + ((id * 7919) % 70) * 1e5,
      status: "done", rating: id === 12 ? 4 : null, taken_at: t, v: 1, error: null, scene });
    facts[id] = { people: false, faces: 0, eyes_closed: false, protected: id === 12, value: 0.5, sharp: scene.blur ? 0.05 : 0.35 + ((id * 37) % 60) / 100, exposure: scene.dark ? 0.2 : 0.95, eyes_known: false, sequence: false, ...extra };
    return id;
  };
  const plan = [[3, 4], [5], [2, 3], [6, 2], [4], [3, 3, 2], [7], [2, 2], [4, 3], [5]];
  let t = DAY0;
  plan.forEach((series, gi) => {
    const motif = MOTIFS[gi % MOTIFS.length];
    const parts = series.map((len, si) => {
      const ids = [];
      for (let k = 0; k < len; k++) {
        ids.push(add({ motif, seed: gi * 7 + 3, shift: k * 0.35 + si * 4, blur: k === 2 && len > 3 ? 3 : 0 }, t));
        t += 2;
      }
      t += 240;
      return ids;
    });
    const ids = parts.flat();
    groups.push({ n: gi + 1, kind: "motif", ids, parts, ranked: parts.map((p) => p[0]).concat(parts.flatMap((p) => p.slice(1))), people: false, start: photos[ids[0] - 1].taken_at, end: photos[ids.at(-1) - 1].taken_at });
    t += 2600;
    if (gi % 2) t += 43000;
    add({ motif: MOTIFS[(gi * 3 + 1) % MOTIFS.length], seed: 100 + gi }, t);
    t += 1900;
  });
  add({ motif: "forest", seed: 301, blur: 9 }, t);
  add({ motif: "village", seed: 305, dark: 0.86 }, t + 90);
  add({ motif: "peaks", seed: 309, blur: 4 }, t + 400);
})();

const inGroup = new Map();
for (const g of groups) for (const part of g.parts) part.forEach((id, k) => inGroup.set(id, { g, part, k }));

function proposal(id) {
  const photo = photos[id - 1];
  if (photo.rating) return { auto: true };
  if (photo.scene.dark) return { auto: false, reason: "Stark unterbelichtet.", code: "underexposed" };
  if (!inGroup.has(id) && photo.scene.blur === 9) return { auto: true, review: true, reason: "Wirkt unscharf – bitte selbst prüfen.", code: "blur_review" };
  const e = inGroup.get(id);
  if (!e) return { auto: true };
  if (shared.mode === "clean") {
    if (e.k === 0) return { auto: true };
    return { auto: false, code: "series", ref: e.part[0], reason: `Fast identisch mit Bild 1 dieser Serie${photo.scene.blur ? ", aber unschärfer" : ""}.` };
  }
  const keep = Math.max(1, Math.round(e.g.ids.length * shared.keep_target * (0.75 + shared.level * 0.08)));
  if (e.g.ranked.indexOf(id) < keep) return { auto: true };
  return { auto: false, code: "motif", ref: e.g.ranked[0], reason: "Gleiches Motiv, ein besseres Bild bleibt." };
}

const present = () => photos.filter((p) => !moved?.ids.includes(p.id));

function decision(id) {
  const p = hold ? { auto: true } : proposal(id);
  const m = manual.get(id) ?? null;
  const keep = m ? m === "keep" : p.auto;
  const review = Boolean(p.review) && !m;
  return { id, keep, auto: p.auto, manual: m, review, auto_review: Boolean(p.review),
    reason: m === "reject" ? "Von Ihnen aussortiert." : m === "keep" ? null : p.reason ?? null, code: m === "reject" ? "manual" : p.code ?? null, ref: m ? null : p.ref ?? null };
}

function stats() {
  const list = present().map((p) => decision(p.id));
  const count = (f) => list.filter(f).length;
  return { total: list.length, keep: count((d) => d.keep), reject: count((d) => !d.keep), review: count((d) => d.review), unanalysed: 0,
    reject_duplicate: 0, reject_series: count((d) => d.code === "series" && !d.keep), reject_motif: count((d) => d.code === "motif" && !d.keep),
    reject_quality: count((d) => d.code === "underexposed" && !d.keep), reject_manual: count((d) => d.manual === "reject"),
    keep_floor: 0.2, target: null, hold, applied: Boolean(moved), count_before: moved ? photos.length : null, ...shared };
}

function structure() {
  const ids = new Set(present().map((p) => p.id));
  const visible = groups.map((g) => ({ ...g, ids: g.ids.filter((i) => ids.has(i)), ranked: g.ranked.filter((i) => ids.has(i)), parts: g.parts.map((p) => p.filter((i) => ids.has(i))).filter((p) => p.length) })).filter((g) => g.ids.length > 1);
  const grouped = new Set(visible.flatMap((g) => g.ids));
  return { groups: visible, singles: present().map((p) => p.id).filter((i) => !grouped.has(i)), duplicates: [], facts };
}

let last = new Map();
function diff(withStructure) {
  const changed = [];
  for (const p of present()) {
    const d = decision(p.id);
    if (JSON.stringify(last.get(p.id)) !== JSON.stringify(d)) changed.push(d);
    last.set(p.id, d);
  }
  return { changed, stats: stats(), ...(withStructure ? structure() : {}) };
}

function applyStatus() {
  return { applied: Boolean(moved), hold, count_before: moved ? photos.length : null, batches: moved ? 1 : 0,
    last: moved ? { batch_id: "demo", created_at: moved.at, state: "applied", photos: moved.ids.length, files: moved.ids.length, pending_count: 0, pending: [] } : null };
}

function runJob(kind, total, finish) {
  job = { kind, batch_id: "demo", state: "running", done: 0, total, current: null, report: null, error: null };
  const timer = setInterval(() => {
    job.done = Math.min(total, job.done + Math.max(1, Math.ceil(total / 14)));
    job.current = photos[(job.done * 3) % photos.length].name;
    if (job.done < total) return;
    clearInterval(timer);
    finish();
    job.report = { kind, batch_id: "demo", photos: total, files: total, failed: [], failed_count: 0, notes: [], cancelled: 0 };
    job.state = "done";
  }, 110);
  return job;
}

/** Progress of the invented analysis: scan, analysis, faces – about 15 seconds. */
class DemoEvents {
  constructor() {
    this.readyState = 1;
    const n = photos.length;
    let tick = 0;
    let sent = 0;
    this.timer = setInterval(() => {
      tick += 1;
      let p;
      if (tick <= 5) p = { stage: "scan", done: 0, total: 0, eta_seconds: null, current_file: null };
      else if (tick <= 50) {
        const d = Math.round(((tick - 5) / 45) * n);
        p = { stage: "analyze", done: d, total: n, eta_seconds: (62 - tick) / 4, current_file: photos[Math.max(0, d - 1)].name };
      } else p = { stage: "faces", done: Math.min(18, Math.round(((tick - 50) / 12) * 18)), total: 18, eta_seconds: (62 - tick) / 4, current_file: photos[tick % n].name };
      const finished = [];
      if (p.stage === "analyze") while (sent < p.done) finished.push(photos[sent++].id);
      if (p.stage === "faces") finished.push(photos[(tick * 5) % n].id);
      this.onmessage?.({ data: JSON.stringify({ state: tick >= 62 ? "done" : "running", progress: p, finished, error: null }) });
      if (tick >= 62) this.close();
    }, 250);
  }
  close() { clearInterval(this.timer); this.readyState = 2; }
}

const cache = new Map();
function picture(id, large) {
  const key = `${id}${large ? "l" : "s"}`;
  if (!cache.has(key)) {
    const c = paint(photos[id - 1].scene, large ? 1200 : 360, large ? 800 : 240);
    cache.set(key, c.toDataURL("image/jpeg", 0.85));
  }
  return cache.get(key);
}

export const api = {
  version: async () => ({ version: "Entwurf" }),
  prefs: async () => ({ design: document.documentElement.dataset.design }),
  setPrefs: async () => ({}),
  recent: async () => [
    { album_id: ALBUM.id, name: ALBUM.name, path: ALBUM.path, photos: photos.length, done: 0, recursive: true, applied: false },
    { album_id: "b", name: "Ostsee 2024", path: "C:\\Beispiel\\Bilder\\Ostsee 2024", photos: 618, done: 618, recursive: true, applied: true },
    { album_id: "c", name: "Wandern Allgäu", path: "C:\\Beispiel\\Bilder\\Wandern Allgäu", photos: 342, done: 342, recursive: true, applied: false },
  ],
  pickFolder: async () => ({ path: ALBUM.path }),
  open: async () => {
    await wait(1700);
    return { album_id: ALBUM.id, name: ALBUM.name, path: ALBUM.path, recursive: true, photos: photos.length, ignored: 3, total_bytes: photos.reduce((a, p) => a + p.size, 0),
      offline: 0, drive: "fixed", few: false, cached: 0, pending: photos.length, faces_pending: 0, estimate_seconds: 15, unreadable_dirs: [], apply: applyStatus() };
  },
  analyze: async () => ({ state: "running" }),
  cancel: async () => ({}),
  events: () => new DemoEvents(),
  view: async () => {
    last = new Map(present().map((p) => [p.id, decision(p.id)]));
    return { album_id: ALBUM.id, name: ALBUM.name, path: ALBUM.path, state: "done", photos: present().map((p, i) => ({ ...p, n: i + 1 })),
      review: { decisions: [...last.values()], stats: stats(), done: [...done], ...structure() }, apply: applyStatus(), job: null };
  },
  info: async (_id, pid) => {
    const p = photos[pid - 1];
    return { id: pid, name: p.name, folder: "", size: p.size, width: p.w, height: p.h, taken_at: p.taken_at, taken_at_source: "exif", camera: "Beispielkamera", exposure_bias: 0, rating: p.rating, status: "done", error: null, companions: [], features: null };
  },
  decision: async (_id, ids, m) => {
    for (const id of ids) { if (m) manual.set(id, m); else manual.delete(id); }
    return diff(false);
  },
  settings: async (_id, changes) => {
    const before = shared.level;
    Object.assign(shared, changes);
    return diff(before !== shared.level);
  },
  groupDone: async (_id, sig, on) => { if (on) done.add(sig); else done.delete(sig); return { ok: true }; },
  seen: async () => ({ ok: true }),
  rethin: async () => { hold = false; return {}; },
  applyPreview: async () => {
    const ids = present().map((p) => p.id).filter((id) => !decision(id).keep);
    const s = stats();
    return { photo_ids: ids, photos: ids.length, files: ids.length, bytes: ids.reduce((a, id) => a + photos[id - 1].size, 0),
      by_reason: { duplicate: 0, series: s.reject_series, motif: s.reject_motif, quality: s.reject_quality, manual: s.reject_manual },
      blocking: [], warnings: ["Entwurf mit Beispielbildern: Es wird nichts wirklich verschoben."], target: `${ALBUM.path}\\_Aussortiert`, count_before: present().length, recorded: false };
  },
  apply: async (_id, ids) => runJob("apply", ids.length, () => { moved = { at: Date.now() / 1000, ids: [...(moved?.ids ?? []), ...ids] }; hold = true; }),
  applyRetry: async () => job,
  applyStatus: async () => ({ job: { ...job }, apply: applyStatus() }),
  undo: async () => runJob("undo", moved.ids.length, () => { moved = null; hold = false; }),
  openFolder: async () => { throw new ApiError(501, "Nur im Programm verfügbar."); },
  fullUrl: (_id, photo) => picture(photo.id, true),
  thumbUrl: (_id, photo, size = "s") => picture(photo.id, size === "l"),
};

export const samples = { count: photos.length, picture };
