// Large view overlay (04 §2): 1600-px preview, arrows, film strip, details panel.
import { api } from "./api.js";
import { cropMarks, fmt, h } from "./util.js";

const STRIP_RADIUS = 8; // frames shown on each side in the film strip

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
  const stage = h("div.viewer-stage", {}, h("div.viewer-frame", {}, img, loading, ...cropMarks()));
  const strip = h("div.strip", { role: "list", "aria-label": "Filmstreifen" });
  const details = h("dl.details");
  const counter = h("span.viewer-counter");
  const title = h("h2.viewer-title");

  const prevBtn = h("button.nav.prev", { type: "button", "aria-label": "Vorheriges Bild (Pfeil links)", onclick: () => show(current - 1) }, "‹");
  const nextBtn = h("button.nav.next", { type: "button", "aria-label": "Nächstes Bild (Pfeil rechts)", onclick: () => show(current + 1) }, "›");
  const closeBtn = h("button.ghost.viewer-close", { type: "button", onclick: close }, "Schließen", h("kbd", {}, "Esc"));

  const root = h(
    "div.viewer",
    { role: "dialog", "aria-modal": "true", "aria-label": "Großansicht" },
    h("header.viewer-bar", {}, counter, title, closeBtn),
    h("div.viewer-body", {}, prevBtn, stage, nextBtn, h("aside.viewer-side", {}, details)),
    strip,
  );

  function row(label, value, mono = false) {
    return [h("dt", {}, label), h(mono ? "dd.mono" : "dd", {}, value ?? "–")];
  }

  async function loadInfo(photo) {
    const token = ++infoToken;
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
    };
    const action = actions[event.key];
    if (action) {
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
