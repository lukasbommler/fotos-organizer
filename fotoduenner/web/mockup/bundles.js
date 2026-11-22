// DESIGN MOCKUP of the bundle view (A5, 04 §1.4). Groups and decisions are faked from capture
// times; the real logic arrives in M4. Usage: /static/mockup/bundles.html?album=<folder path>
import { api } from "../js/api.js";
import { cropMarks, fmt, h } from "../js/util.js";

const SVG = "http://www.w3.org/2000/svg";
const main = document.getElementById("app");
const SERIES_GAP_S = 90;

// ── hand-drawn grease-pencil marks (seeded, so each tile looks slightly different) ──────
function rand(seed) {
  let s = seed % 2147483647 || 1;
  return () => ((s = (s * 48271) % 2147483647) / 2147483647);
}

function svg(cls, d) {
  const el = document.createElementNS(SVG, "svg");
  el.setAttribute("class", cls);
  el.setAttribute("viewBox", "0 0 100 100");
  el.setAttribute("preserveAspectRatio", "none");
  el.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", d);
  el.append(path);
  return el;
}

function circleMark(seed) {
  const r = rand(seed);
  const j = (a) => (r() - 0.5) * a;
  // an ellipse drawn in one stroke that overshoots its start, like a real grease pencil
  return svg("mark-keep", `M ${52 + j(6)} ${6 + j(3)} C ${84 + j(6)} ${5 + j(4)}, ${97 + j(3)} ${28 + j(6)}, ${96 + j(3)} ${52 + j(6)}`
    + ` C ${95 + j(3)} ${80 + j(5)}, ${72 + j(6)} ${96 + j(3)}, ${47 + j(6)} ${95 + j(3)}`
    + ` C ${20 + j(6)} ${94 + j(3)}, ${4 + j(3)} ${74 + j(6)}, ${5 + j(3)} ${48 + j(6)}`
    + ` C ${6 + j(3)} ${22 + j(6)}, ${27 + j(6)} ${4 + j(3)}, ${62 + j(8)} ${9 + j(4)}`);
}

function strikeMark(seed) {
  const r = rand(seed);
  const j = (a) => (r() - 0.5) * a;
  return svg("mark-reject", `M ${7 + j(6)} ${90 + j(6)} C ${35 + j(8)} ${64 + j(8)}, ${60 + j(8)} ${38 + j(8)}, ${94 + j(5)} ${10 + j(6)}`);
}

// ── icons: thin line glyphs, no emoji (D16) ─────────────────────────────────────────────
const ICONS = {
  person: "M50 44a14 14 0 1 0 0-28a14 14 0 0 0 0 28M22 86c2-18 14-28 28-28s26 10 28 28",
  hand: "M34 86V46M34 46V24a6 6 0 0 1 12 0v20M46 42V18a6 6 0 0 1 12 0v24M58 44V24a6 6 0 0 1 12 0v30c0 18-10 32-26 32",
  bracket: "M14 50h72M50 20v60M26 32v36M74 32v36",
  pano: "M10 36c26-8 54-8 80 0v28c-26-8-54-8-80 0z",
  copy: "M30 22h44v52H30zM20 32v52h44",
};

function icon(name, title) {
  const el = document.createElementNS(SVG, "svg");
  el.setAttribute("class", `icon icon-${name}`);
  el.setAttribute("viewBox", "0 0 100 100");
  if (title) {
    const t = document.createElementNS(SVG, "title");
    t.textContent = title;
    el.append(t);
  }
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", ICONS[name]);
  el.append(path);
  return el;
}

// ── fake grouping and decisions (stand-in for M4) ───────────────────────────────────────
function fakeGroups(photos) {
  const groups = [];
  let current = null;
  for (const p of photos.filter((x) => x.status === "done")) {
    if (current && p.taken_at && p.taken_at - current.last <= SERIES_GAP_S) {
      current.items.push(p);
      current.last = p.taken_at;
    } else {
      current = { items: [p], last: p.taken_at };
      groups.push(current);
    }
  }
  const multi = groups.filter((g) => g.items.length >= 2);
  multi.forEach((g, gi) => {
    g.no = gi + 1;
    g.kind = gi === 2 ? "bracket" : gi === 5 ? "copies" : "group";
    g.people = gi % 3 === 0;
    const ref = g.items[0];
    g.items.forEach((p, i) => {
      if (g.kind === "bracket") {
        p.keep = true;
        p.reason = "Belichtungsreihe – bleibt vollständig erhalten";
      } else if (i === 0 || (g.items.length >= 5 && i === 3)) {
        p.keep = true;
        p.reason = i === 0 ? "Bestes Bild der Serie: am schärfsten" : "Behalten: anderer Bildausschnitt";
      } else {
        p.keep = false;
        p.ref = ref.id;
        p.reason = g.kind === "copies" ? `Exakte Kopie von Bild ${ref.n}` : i % 2
          ? `Fast gleich wie Bild ${ref.n}, etwas unschärfer` : `Fast gleich wie Bild ${ref.n}, Augen geschlossen`;
      }
    });
    g.series = g.kind === "group" && g.items.length >= 5 ? [3] : []; // series break before index 3
  });
  if (multi[1]) { multi[1].items[1].review = true; multi[1].items[1].reason = "Bitte prüfen: möglicherweise unscharf"; }
  if (multi[3]) { multi[3].items[1].keep = true; multi[3].items[1].manual = true; multi[3].items[1].reason = "Von Ihnen behalten"; }
  return multi;
}

// ── rendering ───────────────────────────────────────────────────────────────────────────
function thumb(albumId, p) {
  return h("img", { src: api.thumbUrl(albumId, p), alt: "", loading: "lazy", decoding: "async", draggable: "false" });
}

function tally(items) {
  return h("span.tally", { "aria-hidden": "true" },
    items.slice(0, 14).map((p) => h(`span.tick-mark${p.keep ? ".is-keep" : ""}`)));
}

function kindLabel(g) {
  if (g.kind === "bracket") return [icon("bracket"), "Belichtungsreihe"];
  if (g.kind === "copies") return [icon("copy"), "Exakte Kopien"];
  return [];
}

function bundle(albumId, g, total, onToggle) {
  const kept = g.items.filter((p) => p.keep).length;
  const stack = g.items.slice(0, 3);
  return h(`button.bundle${g.kind !== "group" ? `.is-${g.kind}` : ""}`, {
    type: "button", "aria-expanded": "false", dataset: { group: g.no },
    title: `Gruppe ${g.no}: ${g.items.length} Bilder, ${kept} behalten`, onclick: (e) => onToggle(g, e.currentTarget),
  },
  h("span.stack", {}, stack.map((p, i) => h(`span.card.c${i}`, {}, thumb(albumId, p))).reverse(),
    h("span.stack-count", {}, String(g.items.length))),
  h("span.bundle-meta", {},
    h("span.bundle-no", {}, `G${String(g.no).padStart(2, "0")}`),
    h("span.bundle-kept", {}, h("span.keep-glyph", { "aria-hidden": "true" }), `${kept} von ${g.items.length}`),
    g.people ? icon("person", "Personen im Bild") : null,
    g.items.some((p) => p.review) ? h("span.flag", { title: "Enthält „Bitte prüfen“" }, "!") : null,
    g.items.some((p) => p.manual) ? icon("hand", "Von Ihnen geändert") : null),
  h("span.bundle-kind", {}, ...kindLabel(g)),
  tally(g.items));
}

function decisionTile(albumId, p, total, seed) {
  const el = h(`button.tile.decided${p.keep ? ".is-keep" : ".is-reject"}${p.review ? ".is-review" : ""}`, {
    type: "button", dataset: { id: p.id, ref: p.ref ?? "" }, title: `${p.name}\n${p.reason}`,
  },
  h("span.frame-no", {}, fmt.frame(p.n, total)),
  thumb(albumId, p),
  p.keep ? circleMark(seed) : strikeMark(seed),
  p.review ? h("span.review-flag", { "aria-label": "Bitte prüfen" }, "!") : null,
  p.manual ? icon("hand", "Von Ihnen geändert") : null);
  return h("figure.decision", {}, el, h("figcaption.reason", {}, p.reason));
}

function expanded(albumId, g, total) {
  const kept = g.items.filter((p) => p.keep).length;
  const t0 = g.items[0].taken_at;
  const t1 = g.items[g.items.length - 1].taken_at;
  const rows = [];
  let run = h("div.series");
  g.items.forEach((p, i) => {
    if (g.series.includes(i)) { rows.push(run); run = h("div.series"); }
    run.append(decisionTile(albumId, p, total, p.id * 7 + i));
  });
  rows.push(run);
  return h("section.bundle-open", { "aria-label": `Gruppe ${g.no}` }, ...cropMarks(),
    h("header.open-head", {},
      h("span.bundle-no", {}, `GRUPPE ${String(g.no).padStart(2, "0")}`),
      h("span", {}, `${g.items.length} Bilder`),
      h("span", {}, h("span.keep-glyph", { "aria-hidden": "true" }), `${kept} behalten`),
      h("span.mono", {}, `${fmt.time(t0).slice(0, 5)}–${fmt.time(t1).slice(0, 5)}`),
      g.people ? h("span.with-icon", {}, icon("person"), "Personen") : null,
      h("span.grow"),
      h("button.ghost.small", { type: "button" }, "Gruppe ▾"),
      h("button.ghost.small", { type: "button", "data-close": "1" }, "Zuklappen", h("kbd", {}, "Esc"))),
    h("div.series-row", {}, rows));
}

function render(albumId, photos, groups) {
  const total = photos.length;
  const rejected = groups.flatMap((g) => g.items).filter((p) => !p.keep);
  document.getElementById("count-before").textContent = `${fmt.num(total)} Bilder`;
  document.getElementById("count-after").textContent = fmt.num(total - rejected.length);
  document.getElementById("count-delta").textContent = `−${Math.round((rejected.length / total) * 100)} %`;
  document.getElementById("reject-sum").textContent =
    `Aussortiert: ${fmt.num(rejected.length)} Bilder · ${fmt.bytes(rejected.length * 1.2e6)}`;

  const grid = h("div.bundle-grid", { role: "list" });
  const open = new Map();

  function toggle(g, el) {
    if (open.has(g.no)) {
      open.get(g.no).remove();
      open.delete(g.no);
      el.setAttribute("aria-expanded", "false");
      return;
    }
    // insert the open band after the last bundle of el's visual row
    const top = el.getBoundingClientRect().top;
    let anchor = el;
    for (let n = el.nextElementSibling; n; n = n.nextElementSibling) {
      if (!n.classList.contains("bundle")) continue;
      if (Math.abs(n.getBoundingClientRect().top - top) > 2) break;
      anchor = n;
    }
    while (anchor.nextElementSibling?.classList.contains("bundle-open")) anchor = anchor.nextElementSibling;
    const band = expanded(albumId, g, total);
    band.querySelector("[data-close]").addEventListener("click", () => toggle(g, el));
    anchor.after(band);
    open.set(g.no, band);
    el.setAttribute("aria-expanded", "true");
  }

  for (const g of groups) grid.append(bundle(albumId, g, total, toggle));

  main.replaceChildren(
    h("div.toolbar", {},
      h("div.tabs", { role: "tablist" },
        h("button.tab", { type: "button", role: "tab", "aria-selected": "true" }, "Gruppen", h("span.tab-count", {}, fmt.num(groups.length))),
        h("button.tab", { type: "button", role: "tab", "aria-selected": "false" }, "Qualität", h("span.tab-count", {}, "3")),
        h("button.tab", { type: "button", role: "tab", "aria-selected": "false" }, "Einzelbilder", h("span.tab-count", {}, fmt.num(total - groups.reduce((a, g) => a + g.items.length, 0)))),
        h("button.tab", { type: "button", role: "tab", "aria-selected": "false" }, "Alle", h("span.tab-count", {}, fmt.num(total)))),
      h("div.mode", { role: "radiogroup", "aria-label": "Modus" },
        h("button.mode-opt", { type: "button", role: "radio", "aria-checked": "true" }, "Nur Doppelte & Schlechte"),
        h("button.mode-opt", { type: "button", role: "radio", "aria-checked": "false" }, "Zielquote")),
      h("div.strength", { title: "Gruppierung – im Modus „Nur Doppelte & Schlechte“ ohne Wirkung", "aria-disabled": "true" },
        h("span.strength-label", {}, "Gruppierung"),
        h("span.notches", {}, [0, 1, 2, 3, 4].map((i) => h(`span.notch${i === 2 ? ".on" : ""}`))),
        h("span.strength-ends", {}, "fein … grob"))),
    h("div.sheet", {}, grid));

  // open two bundles for the screenshot: one regular, one with a series break
  const big = groups.find((g) => g.series.length) ?? groups[0];
  const els = [...grid.querySelectorAll(".bundle")];
  toggle(big, els[groups.indexOf(big)]);

  grid.addEventListener("mouseover", (e) => {
    const tile = e.target.closest(".tile.is-reject");
    grid.querySelectorAll(".is-ref").forEach((t) => t.classList.remove("is-ref"));
    if (tile?.dataset.ref) grid.querySelector(`.tile[data-id="${tile.dataset.ref}"]`)?.classList.add("is-ref");
  });
}

async function boot() {
  const path = new URLSearchParams(location.search).get("album");
  if (!path) {
    main.replaceChildren(h("p.empty", {}, "Aufruf: /static/mockup/bundles.html?album=<Ordnerpfad>"));
    return;
  }
  const info = await api.open(path, true);
  document.getElementById("crumb").textContent = info.name;
  const view = await api.view(info.album_id);
  render(info.album_id, view.photos, fakeGroups(view.photos));
}

boot().catch((e) => main.replaceChildren(h("p.error", {}, e.message)));
