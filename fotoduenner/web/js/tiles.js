// Decision tile (04 §1.4): green frame + ✓ = keep, red frame + ✕ = sort out, "!" = please check,
// hand = changed by you, star = protected by rating. The reason sits under the tile.
import { api } from "./api.js";
import { icon, sharpGauge } from "./marks.js";
import { fmt, h } from "./util.js";

export function stateWord(d) {
  if (d.unanalysed) return "nicht analysiert";
  if (!d.keep) return "aussortiert";
  return d.review ? "behalten – bitte prüfen" : "behalten";
}

/** One tile inside a <figure> with its one-line reason. `total` sets the frame-number width. */
export function decisionTile(store, id, total) {
  const photo = store.byId.get(id);
  const ready = photo?.status === "done";
  const tile = h("button.tile.decided", { type: "button", tabindex: "-1", dataset: { id } },
    h("span.frame-no", {}, fmt.frame(photo?.n ?? 0, total)),
    ready
      ? h("img", { src: api.thumbUrl(store.albumId, photo), alt: "", loading: "lazy", decoding: "async", draggable: "false" })
      : h("span.tile-empty", {}, photo?.status === "error" ? "Nicht lesbar" : "…"),
    h("span.marks"),
    h("span.tile-foot"));
  if (photo?.status === "error") tile.classList.add("is-error");
  const fig = h("figure.decision", { dataset: { id } }, tile, h("figcaption.reason"));
  fig.tile = tile;
  updateTile(fig, store, false);
  fillFoot(tile, store, id);
  return fig;
}

function fillFoot(tile, store, id) {
  const f = store.fact(id);
  const photo = store.byId.get(id);
  const foot = tile.querySelector(".tile-foot");
  const parts = [];
  if (f?.people) {
    parts.push(h("span.sym", { title: f.faces ? `${f.faces} Person${f.faces === 1 ? "" : "en"} im Bild` : "Person im Bild" },
      icon("person"), f.faces > 1 ? String(f.faces) : null));
  }
  if (f?.eyes_closed) parts.push(h("span.sym", { title: "Augen geschlossen" }, icon("eyesClosed")));
  if (f) parts.push(sharpGauge(f.sharp));
  foot.replaceChildren(...parts);
  if (photo?.rating > 0) tile.append(h("span.rating", { title: `${photo.rating} Sterne – nie automatisch aussortiert` }, "★"));
}

/** Refresh marks, classes and reason after a decision changed; `fresh` plays the pencil stroke. */
export function updateTile(fig, store, fresh = true) {
  const id = Number(fig.dataset.id);
  const d = store.decision(id);
  const photo = store.byId.get(id);
  const tile = fig.tile;
  tile.classList.toggle("is-keep", d.keep && !d.unanalysed);
  tile.classList.toggle("is-reject", !d.keep);
  tile.classList.toggle("is-review", Boolean(d.review));
  tile.classList.toggle("is-manual", Boolean(d.manual));
  tile.classList.toggle("is-fresh", fresh);
  tile.dataset.ref = d.ref ?? "";
  const marks = tile.querySelector(".marks");
  marks.replaceChildren(...[
    d.review ? h("span.review-flag", { title: "Bitte prüfen" }, "!") : null,
    d.manual ? h("span.hand", { title: "Von Ihnen entschieden" }, icon("hand")) : null,
  ].filter(Boolean));
  const reason = d.reason ?? (d.manual === "keep" ? (d.auto_review ? "Von Ihnen geprüft." : "Von Ihnen behalten.") : "");
  fig.querySelector(".reason").textContent = reason;
  const name = photo?.name ?? "";
  tile.title = reason ? `${name}\n${reason}` : name;
  tile.setAttribute("aria-label", `Bild ${photo?.n ?? ""} ${name}: ${stateWord(d)}${reason ? `. ${reason}` : ""}`);
  if (fresh) setTimeout(() => tile.classList.remove("is-fresh"), 400);
}

/** Registry id -> tile figures currently on screen (a photo can appear twice, e.g. copies). */
export class TileRegistry {
  /** `seen` (optional) = SeenTracker that watches which tiles are on screen. */
  constructor(seen = null) { this.map = new Map(); this.seen = seen; }
  add(fig) {
    const id = Number(fig.dataset.id);
    if (!this.map.has(id)) this.map.set(id, new Set());
    this.map.get(id).add(fig);
    this.seen?.observe(fig);
    return fig;
  }
  update(store, ids) {
    for (const id of ids) for (const fig of this.map.get(id) ?? []) updateTile(fig, store, true);
  }
  remove(fig) {
    this.map.get(Number(fig.dataset.id))?.delete(fig);
    this.seen?.unobserve(fig);
  }
  clear() {
    this.map.clear();
    this.seen?.reset();
  }
}
