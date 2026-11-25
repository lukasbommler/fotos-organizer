// "Gruppen" tab: bundles (stacked prints, best on top) that open into a band across the row
// (04 §1.4, approved mockup A17). Series inside a group are separated by a gap and a hairline.
import { api } from "./api.js";
import { icon } from "./marks.js";
import { decisionTile } from "./tiles.js";
import { cropMarks, fill, fmt, h } from "./util.js";

const FIRST_TILES = 12; // large groups: first tiles + "weitere N anzeigen"

const KIND = {
  bracket: ["bracket", "Belichtungsreihe – bleibt vollständig"],
  pano: ["pano", "Panorama-Serie – bleibt vollständig"],
  copies: ["copy", "Exakte Kopien"],
};

export function groupLabel(g) {
  return typeof g.n === "number" ? `G${String(g.n).padStart(2, "0")}` : g.n;
}

function bundleMeta(store, g) {
  const ds = g.ids.map((id) => store.decision(id));
  const kept = ds.filter((d) => d.keep).length;
  return h("span.bundle-meta", {},
    h("span.bundle-no", {}, groupLabel(g)),
    store.isDone(g) ? h("span.done-badge", { title: "Von Ihnen bestätigt" }, "✓ erledigt") : null,
    h("span.bundle-kept", { title: `${kept} von ${g.ids.length} behalten` },
      h("span.keep-glyph", { "aria-hidden": "true" }), `${kept} von ${g.ids.length}`),
    g.people ? icon("person", "Personen im Bild") : null,
    ds.some((d) => d.review) ? h("span.flag", { title: "Enthält „Bitte prüfen“" }, "!") : null,
    ds.some((d) => d.manual) ? icon("hand", "Von Ihnen geändert") : null);
}

function tally(store, g) {
  return h("span.tally", { "aria-hidden": "true" },
    g.ids.slice(0, 14).map((id) => h(`span.tick-mark${store.decision(id).keep ? ".is-keep" : ""}`)));
}

function bundleButton(store, g) {
  const top = g.ranked.length ? g.ranked : g.ids;
  const stack = top.slice(0, 3);
  const kind = KIND[g.kind];
  const el = h(`button.bundle${kind ? `.is-${g.kind}` : ""}`, {
    type: "button", tabindex: "-1", "aria-expanded": "false", dataset: { group: g.n },
  },
  h("span.stack", {},
    stack.map((id, i) => h(`span.card.c${i}`, {},
      h("img", { src: api.thumbUrl(store.albumId, store.byId.get(id)), alt: "", loading: "lazy", decoding: "async", draggable: "false" }))).reverse(),
    h("span.stack-count", {}, String(g.ids.length))),
  bundleMeta(store, g),
  h("span.bundle-kind", {}, ...(kind ? [icon(kind[0]), kind[1]] : [])),
  tally(store, g));
  refreshBundle(store, g, el);
  return el;
}

export function refreshBundle(store, g, el) {
  el.querySelector(".bundle-meta").replaceWith(bundleMeta(store, g));
  el.querySelector(".tally").replaceWith(tally(store, g));
  const kept = g.ids.filter((id) => store.decision(id).keep).length;
  const done = store.isDone(g);
  el.classList.toggle("is-done", done);
  el.setAttribute("aria-label", `Gruppe ${groupLabel(g)}: ${g.ids.length} Bilder, ${kept} behalten${done ? ", erledigt" : ""}`);
  el.title = `${groupLabel(g)} · ${g.ids.length} Bilder · ${kept} behalten – Klick oder Enter klappt auf`;
}

/**
 * Bundle grid, at most one stack open. `actions` = {groupMenu(g, anchor), registry, confirm(g)}.
 */
export function renderBundles(store, groups, actions) {
  const total = store.photos.length;
  const grid = h("div.bundle-grid", { role: "list" });
  const bundles = new Map(); // n -> {g, el, band}

  function openBand(g) {
    const kept = g.ids.filter((id) => store.decision(id).keep).length;
    const rows = h("div.series-row");
    let shown = 0;
    let rest = [];
    for (const part of g.parts) {
      const series = h("div.series");
      for (const id of part) {
        if (shown >= FIRST_TILES) { rest.push([series, id]); continue; }
        series.append(actions.registry.add(decisionTile(store, id, total)));
        shown += 1;
      }
      rows.append(series);
    }
    const more = rest.length
      ? h("button.ghost.small.more", { type: "button", onclick: () => {
        for (const [series, id] of rest) series.append(actions.registry.add(decisionTile(store, id, total)));
        rest = [];
        more.remove();
      } }, `weitere ${fmt.num(rest.length)} anzeigen`)
      : null;
    const kind = KIND[g.kind];
    const menuBtn = h("button.ghost.small", { type: "button", "aria-haspopup": "menu",
      onclick: (e) => actions.groupMenu(g, e.currentTarget) }, "Gruppe", icon("chevron"));
    const closeBtn = h("button.ghost.small", { type: "button", onclick: () => toggle(g) }, "Zuklappen", h("kbd", {}, "Esc"));
    const span = g.start && g.end ? `${fmt.time(g.start).slice(0, 5)}–${fmt.time(g.end).slice(0, 5)}` : "";
    const band = h("section.bundle-open", { "aria-label": `Gruppe ${groupLabel(g)}`, dataset: { group: g.n } }, ...cropMarks(),
      h("header.open-head", {},
        h("span.bundle-no", {}, typeof g.n === "number" ? `GRUPPE ${String(g.n).padStart(2, "0")}` : "EXAKTE KOPIEN"),
        h("span", {}, `${g.ids.length} Bilder`),
        h("span.open-kept", {}, h("span.keep-glyph", { "aria-hidden": "true" }), `${kept} behalten`),
        span ? h("span.mono", {}, span) : null,
        g.people ? h("span.with-icon", {}, icon("person"), "Personen") : null,
        kind ? h("span.with-icon", {}, icon(kind[0]), kind[1]) : null,
        h("span.grow"), menuBtn, closeBtn),
      rows, more,
      h("footer.open-foot"));
    band.update = () => {
      const done = store.isDone(g);
      const rejects = g.ids.filter((id) => !store.decision(id).keep).length;
      band.classList.toggle("is-done", done);
      fill(band.querySelector(".open-foot"),
        h("span.open-sum", {}, `${fmt.num(g.ids.length - rejects)} behalten · ${fmt.num(rejects)} aussortieren`),
        done
          ? [h("span.done-badge", {}, "✓ erledigt"), h("button.ghost.small", { type: "button", onclick: () => store.setDone(g, false) }, "Wieder öffnen")]
          : h("button.primary.confirm", { type: "button", title: "So übernehmen und zum nächsten offenen Stapel", onclick: () => actions.confirm(g) }, "Stapel bestätigen", h("kbd", {}, "B")));
      const k = g.ids.filter((id) => store.decision(id).keep).length;
      band.querySelector(".open-kept").replaceChildren(h("span.keep-glyph", { "aria-hidden": "true" }), `${k} behalten`);
    };
    band.update();
    return band;
  }

  /** Insert the band after the last bundle in the visual row of `el`. */
  function place(entry) {
    const top = entry.el.getBoundingClientRect().top;
    let anchor = entry.el;
    for (let n = entry.el.nextElementSibling; n; n = n.nextElementSibling) {
      if (!n.classList.contains("bundle")) continue;
      if (Math.abs(n.getBoundingClientRect().top - top) > 2) break;
      anchor = n;
    }
    while (anchor.nextElementSibling?.classList.contains("bundle-open") && anchor.nextElementSibling !== entry.band) {
      anchor = anchor.nextElementSibling;
    }
    // moving a node that holds the focus would blur it: only move when the place changed
    if (anchor !== entry.band && anchor.nextElementSibling !== entry.band) anchor.after(entry.band);
  }

  const openOrder = []; // at most one entry
  function shut(entry) {
    for (const fig of entry.band.querySelectorAll("figure.decision")) actions.registry.remove(fig);
    entry.band.remove();
    entry.band = null;
    entry.el.setAttribute("aria-expanded", "false");
    openOrder.splice(openOrder.indexOf(entry.g.n), 1);
  }

  function toggle(g, focusFirst = false) {
    const entry = bundles.get(g.n);
    actions.onToggle?.();
    if (entry.band) { shut(entry); return; }
    const sheet = grid.closest(".sheet");
    const top = entry.el.getBoundingClientRect().top;
    for (const n of [...openOrder]) shut(bundles.get(n)); // one stack at a time
    if (sheet) sheet.scrollTop += entry.el.getBoundingClientRect().top - top; // the clicked stack stays put
    entry.band = openBand(g);
    place(entry);
    entry.el.setAttribute("aria-expanded", "true");
    openOrder.push(g.n);
    if (sheet) {
      const box = sheet.getBoundingClientRect();
      const r = entry.band.getBoundingClientRect();
      // band below the fold: scroll so that the stack sits at the top and its photos are visible
      if (r.bottom > box.bottom) sheet.scrollTop += Math.min(entry.el.getBoundingClientRect().top - box.top - 12, r.bottom - box.bottom + 12);
    }
    if (focusFirst) entry.band.querySelector(".tile")?.focus({ preventScroll: true });
  }

  for (const g of groups) {
    const el = bundleButton(store, g);
    el.addEventListener("click", () => toggle(g));
    bundles.set(g.n, { g, el, band: null });
    grid.append(el);
  }
  const first = grid.querySelector(".bundle");
  if (first) first.tabIndex = 0;

  let resizeTimer = null;
  const observer = new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      for (const n of openOrder) place(bundles.get(n));
    }, 120);
  });
  observer.observe(grid);

  return {
    el: grid,
    toggle,
    bundleOf: (el) => bundles.get(isNaN(el.dataset.group) ? el.dataset.group : Number(el.dataset.group)),
    /** Group ids of the band that contains `tile`, in display order. */
    bandIds(tile) {
      const band = tile.closest(".bundle-open");
      return band ? [...band.querySelectorAll(".tile")].map((t) => Number(t.dataset.id)) : [];
    },
    refresh(ids) {
      const touched = new Set();
      for (const id of ids) {
        const g = store.copyOf.get(id) ?? store.groupOf.get(id);
        if (g) touched.add(g.n);
        for (const cb of store.copyBundles) if (cb.ids.includes(id)) touched.add(cb.n);
      }
      for (const n of touched) {
        const entry = bundles.get(n);
        if (!entry) continue;
        refreshBundle(store, entry.g, entry.el);
        entry.band?.update();
      }
    },
    /** The open stack, if any. */
    openGroup() { return openOrder.length ? bundles.get(openOrder[0]).g : null; },
    /** Next stack after `g` that is not confirmed yet (then from the start); null when all are. */
    nextOpen(g) {
      const list = [...bundles.values()];
      const i = list.findIndex((e) => e.g.n === g.n);
      return [...list.slice(i + 1), ...list.slice(0, i)].find((e) => !store.isDone(e.g)) ?? null;
    },
    refreshGroup(g) {
      const entry = bundles.get(g.n);
      if (!entry) return;
      refreshBundle(store, g, entry.el);
      entry.band?.update();
    },
    /** Close the band that holds the focus, otherwise the one opened last. Returns its bundle. */
    closeFocused() {
      const band = document.activeElement?.closest?.(".bundle-open");
      const n = band ? (isNaN(band.dataset.group) ? band.dataset.group : Number(band.dataset.group)) : openOrder.at(-1);
      if (n === undefined) return null;
      const entry = bundles.get(n);
      toggle(entry.g);
      return entry.el;
    },
    destroy() { observer.disconnect(); },
  };
}
