// Large view overlay (04 §2): 1600-px preview, arrows, film strip, details, decision panel,
// 100 % zoom on the original (Z or click), debug view (D).
import { api } from "./api.js";
import { circleMark, strikeMark } from "./marks.js";
import { stateWord } from "./tiles.js";
import { cropMarks, fill, fmt, h } from "./util.js";

const STRIP_RADIUS = 8; // frames shown on each side in the film strip
const SVG_NS = "http://www.w3.org/2000/svg";
const EYES_OPEN = 0.6; // config.eyes_open_threshold
const ZS_NAMES = {
  person_back: "Person von hinten",
  screenshot: "Screenshot",
  document_sign: "Schild / Dokument",
  blurry: "Unscharf",
};
// The debug view (key D, M3) stays switched on across photos and viewer sessions.
let debugOn = sessionStorage.getItem("fd-debug") === "1";

const isNum = (v) => v !== null && v !== undefined;
const dec = (v, digits = 2) => (isNum(v) ? Number(v).toFixed(digits).replace(".", ",") : "–");
const pct = (v) => (isNum(v) ? `${dec(v * 100, 1)} %` : "–");

/** Face boxes over the image: relative coordinates, so the overlay scales with the image. */
function faceOverlay(faces) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 1 1");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "debug-boxes");
  const labels = [];
  faces.forEach((face, i) => {
    const [x, y, w, hgt] = face.box;
    const rect = document.createElementNS(SVG_NS, "rect");
    for (const [k, v] of Object.entries({ x, y, width: w, height: hgt })) rect.setAttribute(k, v);
    const eyes = face.eyes_open;
    rect.setAttribute("class", !isNum(eyes) ? "face" : eyes >= EYES_OPEN ? "face open" : "face closed");
    svg.append(rect);
    labels.push(
      h("span.debug-face-label", { style: { left: `${x * 100}%`, top: `${(y + hgt) * 100}%` } },
        `${i + 1}  Augen ${dec(eyes)}`),
    );
  });
  return [svg, ...labels];
}

function debugPanel(features) {
  if (!features) return [h("p.debug-note", {}, "Noch keine Messwerte – Bild nicht analysiert.")];
  const rows = (pairs) => h("dl.debug-values", {}, ...pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  const zs = Object.entries(features.zeroshot).map(([name, score]) => {
    const on = features.zeroshot_active.includes(name);
    const off = features.zeroshot_disabled.includes(name);
    return h(`div.zs-row${on ? ".is-on" : ""}${off ? ".is-off" : ""}`, { title: off ? "abgeschaltet (unzuverlässig)" : null },
      h("span.zs-name", {}, `${ZS_NAMES[name] ?? name}${off ? " (aus)" : ""}`),
      h("span.zs-bar", { "aria-hidden": "true" }, h("span", { style: { transform: `scaleX(${score})` } })),
      h("span.zs-val", {}, dec(score)));
  });
  const faces = features.faces.map((f, i) =>
    rows([
      [`Gesicht ${i + 1}`, `${pct(f.size_rel)} breit · ${dec(f.conf)}`],
      ["Schärfe", `${dec(f.sharpness, 0)}${isNum(f.sharpness_native) ? ` · nativ ${dec(f.sharpness_native, 0)}` : ""}`],
      ["Augen offen", isNum(f.eyes_open) ? `${dec(f.eyes_open)} (L ${dec(f.blink?.[0])} / R ${dec(f.blink?.[1])})` : "–"],
    ]));
  return [
    h("h3.debug-head", {}, "Messwerte"),
    rows([
      ["Schärfe Spitze", dec(features.sharpness_peak, 0)],
      ["Schärfe global", dec(features.sharpness_global, 0)],
      ["Helligkeit", `${dec(features.exposure_mean, 0)} · Kontrast ${dec(features.contrast, 0)}`],
      ["Clipping t/h", `${pct(features.clip_low)} / ${pct(features.clip_high)}`],
      ["Belichtung q", dec(features.q_exposure)],
      ["Ästhetik", dec(features.aesthetic)],
      ["pHash", features.phash ?? "–"],
    ]),
    h("h3.debug-head", {}, "Zero-Shot"),
    ...zs,
    h("h3.debug-head", {}, `Gesichter ${features.faces.length}${features.faces_native === 1 ? " · 2. Durchgang" : ""}`),
    ...faces,
  ];
}

function describeRating(rating) {
  if (rating === null || rating === undefined) return "keine";
  if (rating < 0) return "abgelehnt (−1)";
  if (rating === 0) return "0 Sterne";
  return `${"★".repeat(rating)}  geschützt`;
}

/** Measurements in words (04 §2): album-relative, no raw numbers. */
function words(fact, features) {
  if (!fact) return [];
  const out = [];
  const s = fact.sharp;
  out.push(["Schärfe", s >= 0.75 ? "sehr gut" : s >= 0.5 ? "gut" : s >= 0.2 ? "mittel" : "schwach"]);
  if (fact.faces > 0 || fact.people) {
    out.push(["Augen", fact.eyes_closed ? "geschlossen" : fact.eyes_known ? "offen" : "nicht erkennbar"]);
  }
  const e = fact.exposure;
  const bright = features ? features.exposure_mean > 128 : null;
  const dir = bright === null ? "auffällig" : bright ? "zu hell" : "zu dunkel";
  out.push(["Belichtung", e >= 0.85 ? "in Ordnung" : e >= 0.5 ? `etwas ${dir}` : `stark ${dir}`]);
  if (fact.people) out.push(["Personen", fact.faces ? String(fact.faces) : "ja (von hinten)"]);
  if (fact.sequence) out.push(["Folge", "Belichtungsreihe / Panorama – bleibt vollständig"]);
  return out;
}

/**
 * Open the overlay. `photos` = list of photo rows to step through, `store` (optional) =
 * ReviewStore for decisions; without it the view is read-only (analysis still running).
 */
export function openViewer({ albumId, photos, index, onClose, store = null }) {
  let current = index;
  let infoToken = 0;
  let zoomed = false;
  const previous = document.activeElement;

  const img = h("img.viewer-img", { alt: "" });
  const loading = h("span.viewer-loading", { "aria-hidden": "true" });
  const overlay = h("div.debug-overlay", { "aria-hidden": "true" });
  const marks = h("span.viewer-marks", { "aria-hidden": "true" });
  const frame = h("div.viewer-frame", {}, img, marks, overlay, loading, ...cropMarks());
  const zoomImg = h("img.zoom-img", { alt: "", draggable: "false" });
  const zoomBox = h("div.zoom", { hidden: true, tabindex: "-1" }, zoomImg);
  const stage = h("div.viewer-stage", {}, frame, zoomBox);
  const strip = h("div.strip", { role: "list", "aria-label": "Filmstreifen" });
  const decision = h("section.verdict", { "aria-live": "polite" });
  const details = h("dl.details");
  const debug = h("section.debug", { "aria-label": "Messwerte" });
  const counter = h("span.viewer-counter");
  const title = h("h2.viewer-title");

  const prevBtn = h("button.nav.prev", { type: "button", "aria-label": "Vorheriges Bild (Pfeil links)", onclick: () => show(current - 1) }, "‹");
  const nextBtn = h("button.nav.next", { type: "button", "aria-label": "Nächstes Bild (Pfeil rechts)", onclick: () => show(current + 1) }, "›");
  const closeBtn = h("button.ghost.viewer-close", { type: "button", onclick: close }, "Schließen", h("kbd", {}, "Esc"));
  const zoomBtn = h("button.ghost.viewer-zoom", { type: "button", onclick: () => toggleZoom() }, "100 %", h("kbd", {}, "Z"));
  const debugBtn = h("button.ghost.viewer-debug", { type: "button", onclick: () => toggleDebug() }, "Messwerte", h("kbd", {}, "D"));

  const root = h(
    "div.viewer",
    { role: "dialog", "aria-modal": "true", "aria-label": "Großansicht" },
    h("header.viewer-bar", {}, counter, title, zoomBtn, debugBtn, closeBtn),
    h("div.viewer-body", {}, prevBtn, stage, nextBtn, h("aside.viewer-side", {}, decision, details, debug)),
    strip,
  );

  function row(label, value, mono = false) {
    return [h("dt", {}, label), h(mono ? "dd.mono" : "dd", {}, value ?? "–")];
  }

  let lastFeatures = null;

  function renderDebug() {
    root.classList.toggle("is-debug", debugOn);
    debugBtn.setAttribute("aria-pressed", String(debugOn));
    debug.hidden = !debugOn;
    overlay.hidden = !debugOn || zoomed;
    if (!debugOn) return;
    debug.replaceChildren(...debugPanel(lastFeatures));
    overlay.replaceChildren(...(lastFeatures ? faceOverlay(lastFeatures.faces) : []));
  }

  function toggleDebug() {
    debugOn = !debugOn;
    sessionStorage.setItem("fd-debug", debugOn ? "1" : "0");
    renderDebug();
  }

  function renderDecision() {
    const photo = photos[current];
    if (!store || !photo) { decision.hidden = true; return; }
    const d = store.decision(photo.id);
    decision.hidden = false;
    root.classList.toggle("is-reject", !d.keep);
    marks.replaceChildren(...(d.unanalysed ? [] : [d.keep ? circleMark(photo.id) : strikeMark(photo.id)]));
    const ref = d.ref ? store.byId.get(d.ref) : null;
    const pairs = words(store.fact(photo.id), lastFeatures);
    fill(decision, 
      h(`p.verdict-state${d.keep ? ".is-keep" : ".is-reject"}${d.review ? ".is-review" : ""}`, {},
        h("span.verdict-glyph", { "aria-hidden": "true" }), stateWord(d)[0].toUpperCase() + stateWord(d).slice(1),
        d.manual ? h("span.verdict-manual", {}, "· von Ihnen") : null),
      d.reason ? h("p.verdict-reason", {}, d.reason) : null,
      ref ? h("button.link.verdict-ref", { type: "button", onclick: () => jumpTo(ref.id) },
        `Bezugsbild ${fmt.frame(ref.n, store.photos.length)} ansehen`) : null,
      pairs.length ? h("dl.words", {}, ...pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])) : null,
      d.unanalysed ? null : h("div.verdict-actions", {},
        h(`button.ghost.small${d.keep ? ".is-on" : ""}`, { type: "button", onclick: () => store.setKeep([photo.id], true) }, "Behalten", h("kbd", {}, "K")),
        h(`button.ghost.small${!d.keep ? ".is-on" : ""}`, { type: "button", onclick: () => store.setKeep([photo.id], false) }, "Aussortieren", h("kbd", {}, "X")),
        d.manual ? h("button.link", { type: "button", onclick: () => store.setKeep([photo.id], null) }, "Vorschlag wiederherstellen") : null),
    );
  }

  function jumpTo(id) {
    const i = photos.findIndex((p) => p.id === id);
    if (i >= 0) show(i);
    else if (store) {
      photos = [...photos, store.byId.get(id)];
      show(photos.length - 1);
    }
  }

  async function loadInfo(photo) {
    const token = ++infoToken;
    lastFeatures = null;
    renderDebug();
    details.replaceChildren(...row("Datei", photo.name));
    try {
      const info = await api.info(albumId, photo.id);
      if (token !== infoToken) return; // user moved on
      const when = info.taken_at
        ? `${fmt.day(info.taken_at)}, ${fmt.time(info.taken_at)}${info.taken_at_source === "mtime" ? " (Dateidatum)" : ""}`
        : "unbekannt";
      details.replaceChildren(
        ...row("Datei", info.name),
        ...row("Ordner", info.folder || "Hauptordner"),
        ...row("Aufnahme", when),
        ...row("Kamera", info.camera ?? "unbekannt"),
        ...row("Abmessungen", info.width ? `${fmt.num(info.width)} × ${fmt.num(info.height)}` : "–", true),
        ...row("Größe", fmt.bytes(info.size), true),
        ...row("Bewertung", describeRating(info.rating)),
        ...(info.companions.length ? row("Begleitdatei", info.companions.map((c) => c.split("/").pop()).join(", ")) : []),
        ...(info.status === "error" ? row("Status", "Nicht lesbar – wird nie automatisch aussortiert") : []),
      );
      lastFeatures = info.features;
      renderDebug();
      renderDecision();
    } catch (err) {
      if (token === infoToken) details.replaceChildren(...row("Fehler", err.message));
    }
  }

  function renderStrip() {
    const from = Math.max(0, current - STRIP_RADIUS);
    const to = Math.min(photos.length - 1, current + STRIP_RADIUS);
    const frames = [];
    for (let i = from; i <= to; i++) {
      const p = photos[i];
      const rejected = store ? !store.decision(p.id).keep : false;
      frames.push(
        h(`button.strip-frame${rejected ? ".is-reject" : ""}`, {
          type: "button",
          role: "listitem",
          "aria-current": i === current ? "true" : null,
          "aria-label": `Bild ${p.n}`,
          onclick: () => show(i),
        },
        p.status === "done" ? h("img", { src: api.thumbUrl(albumId, p), alt: "", decoding: "async" }) : h("span.tile-empty", {}, "–"),
        h("span.frame-no", {}, fmt.frame(p.n, store?.photos.length ?? photos.length))),
      );
    }
    strip.replaceChildren(...frames);
  }

  // ── 100 % zoom: the original, scrolled to the clicked point ───────────────────────
  function toggleZoom(event) {
    const photo = photos[current];
    if (!photo || photo.status !== "done") return;
    zoomed = !zoomed;
    root.classList.toggle("is-zoomed", zoomed);
    zoomBtn.setAttribute("aria-pressed", String(zoomed));
    frame.hidden = zoomed;
    zoomBox.hidden = !zoomed;
    renderDebug();
    if (!zoomed) { zoomImg.removeAttribute("src"); return; }
    let fx = 0.5;
    let fy = 0.5;
    if (event) {
      const r = img.getBoundingClientRect();
      fx = Math.min(1, Math.max(0, (event.clientX - r.left) / r.width));
      fy = Math.min(1, Math.max(0, (event.clientY - r.top) / r.height));
    }
    root.classList.add("is-loading");
    zoomImg.onload = () => {
      root.classList.remove("is-loading");
      zoomBox.scrollLeft = fx * zoomImg.naturalWidth - zoomBox.clientWidth / 2;
      zoomBox.scrollTop = fy * zoomImg.naturalHeight - zoomBox.clientHeight / 2;
      zoomBox.focus({ preventScroll: true });
    };
    zoomImg.onerror = () => root.classList.remove("is-loading");
    zoomImg.src = api.fullUrl(albumId, photo);
  }

  // drag to pan in the zoom
  let drag = null;
  zoomBox.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, l: zoomBox.scrollLeft, t: zoomBox.scrollTop, moved: false };
    zoomBox.setPointerCapture(e.pointerId);
  });
  zoomBox.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    zoomBox.scrollLeft = drag.l - dx;
    zoomBox.scrollTop = drag.t - dy;
  });
  zoomBox.addEventListener("pointerup", () => {
    if (drag && !drag.moved) toggleZoom(); // a plain click leaves the zoom
    drag = null;
  });
  img.addEventListener("click", (e) => toggleZoom(e));

  function show(i) {
    if (i < 0 || i >= photos.length) return;
    if (zoomed) toggleZoom();
    current = i;
    const photo = photos[i];
    counter.textContent = `${i + 1} / ${photos.length}`;
    title.textContent = `${fmt.frame(photo.n, store?.photos.length ?? photos.length)} · ${photo.name}`;
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === photos.length - 1;
    zoomBtn.disabled = photo.status !== "done";
    root.classList.add("is-loading");
    if (photo.status === "done") {
      img.hidden = false;
      img.src = api.thumbUrl(albumId, photo, "l");
    } else {
      img.hidden = true;
      img.removeAttribute("src");
      root.classList.remove("is-loading");
    }
    stage.classList.toggle("is-error", photo.status === "error");
    renderStrip();
    renderDecision();
    loadInfo(photo);
    for (const n of [i + 1, i - 1]) {
      const p = photos[n];
      if (p?.status === "done") new Image().src = api.thumbUrl(albumId, p, "l");
    }
  }

  img.addEventListener("load", () => root.classList.remove("is-loading"));
  img.addEventListener("error", () => root.classList.remove("is-loading"));

  const unsubscribe = store?.subscribe((event) => {
    if (event.type !== "decisions") return;
    renderDecision();
    renderStrip();
  });

  function decide(keep) {
    const photo = photos[current];
    if (!store || !photo) return;
    if (keep === "toggle") store.toggle(photo.id);
    else store.setKeep([photo.id], keep);
  }

  function onKey(event) {
    if (event.ctrlKey && (event.key === "z" || event.key === "Z") && store) {
      event.preventDefault();
      event.stopPropagation();
      store.undo();
      return;
    }
    const actions = {
      ArrowRight: () => show(current + 1),
      ArrowLeft: () => show(current - 1),
      Home: () => show(0),
      End: () => show(photos.length - 1),
      Escape: () => (zoomed ? toggleZoom() : close()),
      d: toggleDebug,
      D: toggleDebug,
      z: () => toggleZoom(),
      Z: () => toggleZoom(),
      k: () => decide(true),
      K: () => decide(true),
      x: () => decide(false),
      X: () => decide(false),
      " ": () => decide("toggle"),
    };
    const action = actions[event.key];
    if (action && !(event.ctrlKey || event.altKey || event.metaKey)) {
      event.preventDefault();
      event.stopPropagation();
      action();
    }
  }

  function close() {
    document.removeEventListener("keydown", onKey, true);
    unsubscribe?.();
    root.classList.add("is-closing");
    const done = () => root.remove();
    root.addEventListener("animationend", done, { once: true });
    setTimeout(done, 250);
    if (onClose) onClose(photos[current]?.id);
    else previous?.focus?.({ preventScroll: true });
  }

  document.addEventListener("keydown", onKey, true);
  document.body.append(root);
  show(current);
  closeBtn.focus();
  return { close };
}
