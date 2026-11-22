// Contact sheet ("Alle"): day sections, lazy previews, roving keyboard focus (04 §1.4, §4).
import { api } from "./api.js";
import { fmt, h } from "./util.js";

export const TILE_SIZES = [120, 160, 240];

/** One tile. Decisions (circle / strike) arrive in M5; M2 shows status only. */
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
      ? h("img", {
          src: api.thumbUrl(albumId, photo),
          alt: "",
          loading: "lazy",
          decoding: "async",
          draggable: "false",
        })
      : h("span.tile-empty", {}, unreadable ? "Nicht lesbar" : "…"),
    photo.rating > 0 ? h("span.rating", { title: `${photo.rating} Sterne – geschützt` }, "★") : null,
    h("span.tile-name", {}, photo.name),
  );
  if (unreadable) el.classList.add("is-error");
  return el;
}

function dayKey(t) {
  return t ? Math.floor(t / 86400) : "none";
}

/**
 * Render the sheet into `container`. Returns {focus(index), destroy()}.
 * `onOpen(index)` opens the detail view.
 */
export function renderSheet(container, albumId, photos, onOpen) {
  container.replaceChildren();
  const total = photos.length;
  const tiles = [];
  const sections = []; // {start, len}
  const sectionOf = [];
  const counts = new Map();
  for (const p of photos) counts.set(dayKey(p.taken_at), (counts.get(dayKey(p.taken_at)) ?? 0) + 1);
  let section = null;
  let lastKey = null;
  photos.forEach((photo, index) => {
    const key = dayKey(photo.taken_at);
    if (key !== lastKey) {
      lastKey = key;
      const count = counts.get(key);
      sections.push({ start: index, len: count });
      const grid = h("div.sheet-grid", { role: "list" });
      section = h(
        "section.day",
        {},
        h("header.day-head", {},
          h("span.day-date", {}, fmt.day(photo.taken_at)),
          h("span.day-rule", { "aria-hidden": "true" }),
          h("span.day-count", {}, `${fmt.num(count)} Bilder`)),
        grid,
      );
      section.grid = grid;
      container.append(section);
    }
    const el = tile(albumId, photo, total);
    el.dataset.index = String(index);
    el.setAttribute("role", "listitem");
    section.grid.append(el);
    tiles.push(el);
    sectionOf.push(sections.length - 1);
  });

  let current = -1;
  function focus(index) {
    if (!tiles.length) return;
    const next = Math.max(0, Math.min(tiles.length - 1, index));
    if (current >= 0) tiles[current].tabIndex = -1;
    current = next;
    tiles[current].tabIndex = 0;
    tiles[current].focus({ preventScroll: true });
    tiles[current].scrollIntoView({ block: "nearest" });
  }

  function columns() {
    const grid = tiles[current]?.parentElement;
    if (!grid) return 1;
    return getComputedStyle(grid).gridTemplateColumns.split(" ").length || 1;
  }

  /** Up/down within the visual grid; crosses into the neighbouring day keeping the column. */
  function vertical(step) {
    const cols = columns();
    const s = sectionOf[current];
    const { start, len } = sections[s];
    const pos = current - start;
    const row = Math.floor(pos / cols);
    const col = pos % cols;
    const lastRow = Math.floor((len - 1) / cols);
    if (step > 0) {
      if (row < lastRow) return focus(Math.min(current + cols, start + len - 1));
      const next = sections[s + 1];
      if (next) focus(next.start + Math.min(col, next.len - 1));
    } else {
      if (row > 0) return focus(current - cols);
      const prev = sections[s - 1];
      if (!prev) return;
      const rowStart = prev.start + Math.floor((prev.len - 1) / cols) * cols;
      focus(Math.min(rowStart + col, prev.start + prev.len - 1));
    }
  }

  function onKey(event) {
    if (current < 0) return;
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
    if (!el) return;
    focus(Number(el.dataset.index));
  }

  function onDblClick(event) {
    const el = event.target.closest(".tile");
    if (el) onOpen(Number(el.dataset.index));
  }

  container.addEventListener("keydown", onKey);
  container.addEventListener("click", onClick);
  container.addEventListener("dblclick", onDblClick);
  if (tiles.length) tiles[0].tabIndex = 0;
  current = tiles.length ? 0 : -1;

  return {
    focus,
    get current() { return current; },
    destroy() {
      container.removeEventListener("keydown", onKey);
      container.removeEventListener("click", onClick);
      container.removeEventListener("dblclick", onDblClick);
    },
  };
}
