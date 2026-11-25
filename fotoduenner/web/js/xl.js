// XL preview (right click on a tile): the photo large next to its stack, not modal. It closes with
// another right click, a click on any photo, or Esc; the tiles stay visible and K / X keep working.
import { api } from "./api.js";
import { LARGE_DWELL_MS, SEEN } from "./seen.js";
import { stateWord } from "./tiles.js";
import { fill, fmt, h } from "./util.js";

const GAP = 16; // px between the preview, its tile and the window edge
const WIDTH = 0.62; // at least this share of the window; photos beside it stay clickable

export function createXl(store) {
  let current = null; // photo id
  let el = null;
  let seenTimer = null;

  const img = h("img.xl-img", { alt: "", draggable: "false" });
  const info = h("footer.xl-info");
  img.addEventListener("load", () => {
    el?.classList.remove("is-loading");
    clearTimeout(seenTimer);
    const id = current;
    seenTimer = setTimeout(() => { if (id !== null && id === current) store.seen?.mark(id, SEEN.large); }, LARGE_DWELL_MS);
  });

  function render() {
    if (!el) return;
    const d = store.decision(current);
    const photo = store.byId.get(current);
    el.classList.toggle("is-keep", d.keep && !d.unanalysed);
    el.classList.toggle("is-reject", !d.keep);
    el.classList.toggle("is-review", Boolean(d.review));
    const button = (label, kbd, on, keep) => h(`button.ghost.small${on ? ".is-on" : ""}`, { type: "button", tabindex: "-1",
      onclick: () => store.setKeep([current], keep) }, label, kbd ? h("kbd", {}, kbd) : null);
    fill(info,
      h("span.xl-state", {}, stateWord(d), d.manual ? " · von Ihnen" : null),
      h("span.xl-reason", { title: d.reason ?? "" }, d.reason ?? ""),
      h("span.xl-name.mono", {}, `${fmt.frame(photo.n, store.photos.length)} · ${photo.name}`),
      d.unanalysed ? null : button("Behalten", "K", d.keep, true),
      d.unanalysed ? null : button("Aussortieren", "X", !d.keep, false),
      d.manual ? button("Vorschlag", null, false, null) : null);
  }

  function close() {
    clearTimeout(seenTimer);
    document.querySelectorAll(".tile.is-xl").forEach((t) => t.classList.remove("is-xl"));
    el?.remove();
    el = null;
    current = null;
  }

  function open(tile) {
    const id = Number(tile.dataset.id);
    const photo = store.byId.get(id);
    if (photo?.status !== "done") return;
    close();
    current = id;
    tile.classList.add("is-xl");
    const r = tile.getBoundingClientRect();
    // on the side with more room
    const side = r.left > innerWidth - r.right ? "left" : "right";
    const room = Math.max(r.left, innerWidth - r.right) - GAP * 2;
    el = h(`aside.xl.is-loading.on-${side}`, { role: "dialog", "aria-label": "Große Ansicht" }, h("div.xl-frame", {}, img), info);
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); close(); });
    el.addEventListener("pointerdown", (e) => { if (e.button === 0) e.preventDefault(); }); // keep the focus on the tile
    img.removeAttribute("src");
    img.src = api.thumbUrl(store.albumId, photo, "l");
    el.style.width = `${Math.round(Math.max(Math.min(room, innerWidth * 0.7), innerWidth * WIDTH))}px`;
    document.body.append(el);
    render();
  }

  return {
    get id() { return current; },
    get isOpen() { return el !== null; },
    open,
    close,
    render,
  };
}
