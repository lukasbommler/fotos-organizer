// Compare view (04 §2): 2–4 photos side by side, each with its decision and its own 100 % zoom.
// Synchronous zoom/pan comes later (T14).
import { api } from "./api.js";
import { circleMark, strikeMark } from "./marks.js";
import { stateWord } from "./tiles.js";
import { fmt, h } from "./util.js";

export function openCompare({ store, ids, onClose }) {
  const panes = [];
  let active = 0;

  function pane(id, i) {
    const photo = store.byId.get(id);
    const img = h("img.compare-img", { src: api.thumbUrl(store.albumId, photo, "l"), alt: "", draggable: "false" });
    const marks = h("span.viewer-marks", { "aria-hidden": "true" });
    const box = h("div.compare-box", {}, img, marks);
    const info = h("div.compare-info");
    const el = h("section.compare-pane", { tabindex: "0", "aria-label": `Bild ${photo.n}` },
      h("header.compare-head", {}, h("span.mono", {}, `${i + 1}`), h("span.compare-name", {}, `${fmt.frame(photo.n, store.photos.length)} · ${photo.name}`)),
      box, info);
    let zoomed = false;
    let drag = null;
    box.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, l: box.scrollLeft, t: box.scrollTop, moved: false, ex: e };
      box.setPointerCapture(e.pointerId);
    });
    box.addEventListener("pointermove", (e) => {
      if (!drag || !zoomed) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      box.scrollLeft = drag.l - dx;
      box.scrollTop = drag.t - dy;
    });
    box.addEventListener("pointerup", (e) => {
      if (drag && !drag.moved) zoom(e);
      drag = null;
    });
    function zoom(event) {
      zoomed = !zoomed;
      el.classList.toggle("is-zoomed", zoomed);
      if (!zoomed) { img.src = api.thumbUrl(store.albumId, photo, "l"); return; }
      const r = img.getBoundingClientRect();
      const fx = event ? (event.clientX - r.left) / r.width : 0.5;
      const fy = event ? (event.clientY - r.top) / r.height : 0.5;
      el.classList.add("is-loading");
      img.onload = () => {
        img.onload = null;
        el.classList.remove("is-loading");
        box.scrollLeft = fx * img.naturalWidth - box.clientWidth / 2;
        box.scrollTop = fy * img.naturalHeight - box.clientHeight / 2;
      };
      img.src = api.fullUrl(store.albumId, photo);
    }
    function render() {
      const d = store.decision(id);
      el.classList.toggle("is-reject", !d.keep);
      marks.replaceChildren(d.keep ? circleMark(id) : strikeMark(id));
      info.replaceChildren(
        h(`p.verdict-state${d.keep ? ".is-keep" : ".is-reject"}${d.review ? ".is-review" : ""}`, {},
          h("span.verdict-glyph", { "aria-hidden": "true" }), stateWord(d), d.manual ? h("span.verdict-manual", {}, "· von Ihnen") : null),
        h("p.verdict-reason", {}, d.reason ?? " "),
        h("div.verdict-actions", {},
          h(`button.ghost.small${d.keep ? ".is-on" : ""}`, { type: "button", onclick: () => store.setKeep([id], true) }, "Behalten"),
          h(`button.ghost.small${!d.keep ? ".is-on" : ""}`, { type: "button", onclick: () => store.setKeep([id], false) }, "Aussortieren")));
    }
    el.addEventListener("focus", () => { active = i; });
    el.addEventListener("pointerenter", () => { active = i; });
    render();
    return { el, id, render, zoom };
  }

  ids.slice(0, 4).forEach((id, i) => panes.push(pane(id, i)));
  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    unsubscribe();
    root.remove();
    onClose?.();
  };
  const root = h("div.viewer.compare", { role: "dialog", "aria-modal": "true", "aria-label": "Vergleich" },
    h("header.viewer-bar", {},
      h("span.viewer-counter", {}, `Vergleich · ${panes.length} Bilder`),
      h("span.viewer-title", {}, "Klick ins Bild: 100 % · K / X für das Bild unter der Maus · 1–4 wählt ein Bild"),
      h("button.ghost.viewer-close", { type: "button", onclick: close }, "Schließen", h("kbd", {}, "Esc"))),
    h(`div.compare-grid.n${panes.length}`, {}, panes.map((p) => p.el)));

  const unsubscribe = store.subscribe((event) => {
    if (event.type === "decisions") panes.forEach((p) => p.render());
  });

  function onKey(event) {
    if (event.ctrlKey && (event.key === "z" || event.key === "Z")) {
      event.preventDefault();
      event.stopPropagation();
      store.undo();
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const p = panes[active];
    const actions = {
      Escape: close,
      k: () => store.setKeep([p.id], true),
      K: () => store.setKeep([p.id], true),
      x: () => store.setKeep([p.id], false),
      X: () => store.setKeep([p.id], false),
      " ": () => store.toggle(p.id),
      z: () => p.zoom(),
      Z: () => p.zoom(),
    };
    if (/^[1-4]$/.test(event.key) && panes[Number(event.key) - 1]) {
      active = Number(event.key) - 1;
      panes[active].el.focus();
      event.preventDefault();
      return;
    }
    const action = actions[event.key];
    if (action) {
      event.preventDefault();
      event.stopPropagation();
      action();
    }
  }

  document.addEventListener("keydown", onKey, true);
  document.body.append(root);
  panes[0]?.el.focus();
  return { close };
}
