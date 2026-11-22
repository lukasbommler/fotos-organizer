// Large view overlay (04 §2): 1600-px preview, arrows, film strip, details panel.
import { api } from "./api.js";
import { cropMarks, fmt, h } from "./util.js";

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

export function openViewer({ albumId, photos, index, onClose }) {
  let current = index;
  let infoToken = 0;
  const previous = document.activeElement;

  const img = h("img.viewer-img", { alt: "" });
  const loading = h("span.viewer-loading", { "aria-hidden": "true" });
  const overlay = h("div.debug-overlay", { "aria-hidden": "true" });
  const stage = h("div.viewer-stage", {}, h("div.viewer-frame", {}, img, overlay, loading, ...cropMarks()));
  const strip = h("div.strip", { role: "list", "aria-label": "Filmstreifen" });
  const details = h("dl.details");
  const debug = h("section.debug", { "aria-label": "Messwerte" });
  const counter = h("span.viewer-counter");
  const title = h("h2.viewer-title");

  const prevBtn = h("button.nav.prev", { type: "button", "aria-label": "Vorheriges Bild (Pfeil links)", onclick: () => show(current - 1) }, "‹");
  const nextBtn = h("button.nav.next", { type: "button", "aria-label": "Nächstes Bild (Pfeil rechts)", onclick: () => show(current + 1) }, "›");
  const closeBtn = h("button.ghost.viewer-close", { type: "button", onclick: close }, "Schließen", h("kbd", {}, "Esc"));
  const debugBtn = h("button.ghost.viewer-debug", { type: "button", onclick: () => toggleDebug() }, "Messwerte", h("kbd", {}, "D"));

  const root = h(
    "div.viewer",
    { role: "dialog", "aria-modal": "true", "aria-label": "Großansicht" },
    h("header.viewer-bar", {}, counter, title, debugBtn, closeBtn),
    h("div.viewer-body", {}, prevBtn, stage, nextBtn, h("aside.viewer-side", {}, details, debug)),
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
    overlay.hidden = !debugOn;
    if (!debugOn) return;
    debug.replaceChildren(...debugPanel(lastFeatures));
    overlay.replaceChildren(...(lastFeatures ? faceOverlay(lastFeatures.faces) : []));
  }

  function toggleDebug() {
    debugOn = !debugOn;
    sessionStorage.setItem("fd-debug", debugOn ? "1" : "0");
    renderDebug();
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
      frames.push(
        h("button.strip-frame", {
          type: "button",
          role: "listitem",
          "aria-current": i === current ? "true" : null,
          "aria-label": `Bild ${p.n}`,
          onclick: () => show(i),
        },
        p.status === "done" ? h("img", { src: api.thumbUrl(albumId, p), alt: "", decoding: "async" }) : h("span.tile-empty", {}, "–"),
        h("span.frame-no", {}, fmt.frame(p.n, photos.length))),
      );
    }
    strip.replaceChildren(...frames);
  }

  function show(i) {
    if (i < 0 || i >= photos.length) return;
    current = i;
    const photo = photos[i];
    counter.textContent = `${fmt.frame(photo.n, photos.length)} / ${fmt.frame(photos.length, photos.length)}`;
    title.textContent = photo.name;
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === photos.length - 1;
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
    loadInfo(photo);
    // warm the cache for the neighbours
    for (const n of [i + 1, i - 1]) {
      const p = photos[n];
      if (p?.status === "done") new Image().src = api.thumbUrl(albumId, p, "l");
    }
  }

  img.addEventListener("load", () => root.classList.remove("is-loading"));
  img.addEventListener("error", () => root.classList.remove("is-loading"));

  function onKey(event) {
    const actions = {
      ArrowRight: () => show(current + 1),
      ArrowLeft: () => show(current - 1),
      Home: () => show(0),
      End: () => show(photos.length - 1),
      Escape: close,
      d: toggleDebug,
      D: toggleDebug,
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
    root.classList.add("is-closing");
    const done = () => root.remove();
    root.addEventListener("animationend", done, { once: true });
    setTimeout(done, 250);
    if (onClose) onClose(current); // the caller restores focus to the photo last viewed
    else previous?.focus?.({ preventScroll: true });
  }

  document.addEventListener("keydown", onKey, true);
  document.body.append(root);
  show(current);
  closeBtn.focus();
  return { close };
}
