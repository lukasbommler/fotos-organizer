// Small helpers shared by all screens. No innerHTML with data: file names are untrusted text.

const NUM = new Intl.NumberFormat("de-DE");
const DATE = new Intl.DateTimeFormat("de-DE", {
  weekday: "short", day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC",
});
const TIME = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" });
// Program events (Übernehmen) are real timestamps: local time, unlike EXIF times.
const WHEN = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** Create an element: h("div.tile.is-error", {title: "x", onclick}, child, "text", ...) */
export function h(spec, attrs = {}, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replace the children of `el`, skipping null/false entries (replaceChildren would print "null"). */
export function fill(el, ...children) {
  el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

/** Four corner crop marks (D16), placed by CSS just outside the parent. */
export function cropMarks() {
  return ["tl", "tr", "bl", "br"].map((c) => h(`span.crop.${c}`, { "aria-hidden": "true" }));
}

export const fmt = {
  num: (n) => NUM.format(n ?? 0),
  bytes(n) {
    if (!n) return "0 MB";
    if (n >= 1e9) return `${NUM.format(Math.round(n / 1e8) / 10)} GB`;
    const mb = n / 1e6;
    return `${NUM.format(mb < 10 ? Math.max(0.1, Math.round(mb * 10) / 10) : Math.round(mb))} MB`;
  },
  duration(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} s`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m} min`;
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
  },
  /** EXIF times are stored "as UTC" without a zone (A15) – format them in UTC to show them unchanged. */
  day: (t) => (t ? DATE.format(new Date(t * 1000)) : "Ohne Datum"),
  time: (t) => (t ? TIME.format(new Date(t * 1000)) : "–"),
  when: (t) => (t ? `${WHEN.format(new Date(t * 1000))} Uhr` : "–"),
  frame: (n, total) => String(n).padStart(Math.max(3, String(total).length), "0"),
};

/** Run fn at most every `ms` milliseconds (last call wins). */
export function throttle(fn, ms) {
  let timer = null;
  let lastArgs = null;
  return (...args) => {
    lastArgs = args;
    if (timer) return;
    timer = setTimeout(() => { timer = null; fn(...lastArgs); }, ms);
  };
}

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
