// Contact sheet: titled sections of tiles, lazy previews, roving keyboard focus (04 §1.4, §4).
import { api } from "./api.js";
import { fmt, h } from "./util.js";

export const TILE_SIZES = [120, 160, 240];

/** Plain tile without decision (live grid while analysing). */
export function tile(albumId, photo, total) {
  const unreadable = photo.status === "error";
  const ready = photo.status === "done";
  const el = h(
    "button.tile",
    {
      type: "button",
      tabindex: "-1",
      dataset: { id: photo.id },
      title: unreadable ? `${photo.name} – nicht lesbar` : photo.name,
      "aria-label": `Bild ${photo.n}: ${photo.name}${unreadable ? ", nicht lesbar" : ""}`,
    },
    h("span.frame-no", {}, fmt.frame(photo.n, total)),
    ready
      ? h("img", { src: api.thumbUrl(albumId, photo), alt: "", loading: "lazy", decoding: "async", draggable: "false" })
      : h("span.tile-empty", {}, unreadable ? "Nicht lesbar" : "…"),
    photo.rating > 0 ? h("span.rating", { title: `${photo.rating} Sterne – geschützt` }, "★") : null,
  );
  if (unreadable) el.classList.add("is-error");
  return el;
}

const dayKey = (t) => (t ? Math.floor(t / 86400) : "none");

/** Sections by capture day for a list of photos (already in capture order). */
export function daySections(photos) {
  const sections = [];
  let last = null;
  for (const p of photos) {
    const key = dayKey(p.taken_at);
    if (key !== last) {
      last = key;
      sections.push({ title: fmt.day(p.taken_at), ids: [] });
    }
    sections.at(-1).ids.push(p.id);
  }
  return sections;
}

/**
 * Render `sections` ([{title, note?, ids}]) into `container`; `makeTile(id)` returns an element
 * with `.tile` inside (or the tile itself). Returns {focus(index), current, tiles, destroy()}.
 * `onOpen(index)` opens the detail view (Enter; double clicks are handled by the caller).
 */
export function renderSheet(container, sections, makeTile, onOpen) {
  container.replaceChildren();
  const tiles = []; // focusable .tile elements in visual order
  const ranges = [];
  for (const sec of sections) {
    if (!sec.ids.length) continue;
    const grid = h("div.sheet-grid", { role: "list" });
    const start = tiles.length;
    for (const id of sec.ids) {
      const el = makeTile(id);
      const t = el.classList.contains("tile") ? el : el.querySelector(".tile");
      t.dataset.index = String(tiles.length);
      el.setAttribute("role", "listitem");
      grid.append(el);
      tiles.push(t);
    }
    ranges.push({ start, len: tiles.length - start });
    container.append(h("section.day", {},
      h("header.day-head", {},
        h("span.day-date", {}, sec.title),
        h("span.day-rule", { "aria-hidden": "true" }),
        h("span.day-count", {}, sec.note ?? `${fmt.num(sec.ids.length)} Bilder`)),
      grid));
  }
  const rangeOf = [];
  ranges.forEach((r, s) => { for (let i = 0; i < r.len; i++) rangeOf.push(s); });

  let current = -1;
  function focus(index) {
    if (!tiles.length) return;
    const next = Math.max(0, Math.min(tiles.length - 1, index));
    if (current >= 0 && tiles[current]) tiles[current].tabIndex = -1;
    current = next;
    tiles[current].tabIndex = 0;
    tiles[current].focus({ preventScroll: true });
    tiles[current].scrollIntoView({ block: "nearest" });
  }

  function columns() {
    const grid = tiles[current]?.closest(".sheet-grid");
    if (!grid) return 1;
    return getComputedStyle(grid).gridTemplateColumns.split(" ").length || 1;
  }

  /** Up/down within the visual grid; crosses into the neighbouring section keeping the column. */
  function vertical(step) {
    const cols = columns();
    const s = rangeOf[current];
    const { start, len } = ranges[s];
    const pos = current - start;
    const row = Math.floor(pos / cols);
    const col = pos % cols;
    const lastRow = Math.floor((len - 1) / cols);
    if (step > 0) {
      if (row < lastRow) return focus(Math.min(current + cols, start + len - 1));
      const next = ranges[s + 1];
      if (next) focus(next.start + Math.min(col, next.len - 1));
    } else {
      if (row > 0) return focus(current - cols);
      const prev = ranges[s - 1];
      if (!prev) return;
      const rowStart = prev.start + Math.floor((prev.len - 1) / cols) * cols;
      focus(Math.min(rowStart + col, prev.start + prev.len - 1));
    }
  }

  function onKey(event) {
    if (current < 0 || event.ctrlKey || event.altKey || event.metaKey) return;
    const page = Math.max(1, columns() * 3);
    const keys = {
      ArrowRight: () => focus(current + 1),
      ArrowLeft: () => focus(current - 1),
      ArrowDown: () => vertical(1),
      ArrowUp: () => vertical(-1),
      Home: () => focus(0),
      End: () => focus(tiles.length - 1),
      PageDown: () => focus(current + page),
      PageUp: () => focus(current - page),
      Enter: () => onOpen(current),
    };
    const action = keys[event.key];
    if (action) {
      event.preventDefault();
      action();
    }
  }

  function onClick(event) {
    const el = event.target.closest(".tile");
    if (el?.dataset.index) focus(Number(el.dataset.index));
  }

  container.addEventListener("keydown", onKey);
  container.addEventListener("click", onClick);
  if (tiles.length) tiles[0].tabIndex = 0;
  current = tiles.length ? 0 : -1;

  return {
    focus,
    tiles,
    get current() { return current; },
    destroy() {
      container.removeEventListener("keydown", onKey);
      container.removeEventListener("click", onClick);
    },
  };
}
