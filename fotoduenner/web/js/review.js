// Review screen (04 §1.4, §2): tabs, bundles, decision tiles, mode + sliders, filters,
// keyboard, context menu, compare, large view. Decisions live in ReviewStore (store.js).
import { renderBundles } from "./bundles.js";
import { openCompare } from "./compare.js";
import { api } from "./api.js";
import { applyFlow, retryFlow, undoFlow } from "./dialogs.js";
import { daySections, renderSheet, TILE_SIZES } from "./grid.js";
import { SeenTracker } from "./seen.js";
import { ReviewStore } from "./store.js";
import { decisionTile, TileRegistry } from "./tiles.js";
import { fill, fmt, h, throttle } from "./util.js";
import { openViewer } from "./viewer.js";
import { createXl } from "./xl.js";

const QUALITY = new Set(["rating_rejected", "misfire", "overexposed", "underexposed", "blur", "blur_review", "person_quality"]);
const LEVEL_NAMES = ["sehr fein", "fein", "mittel", "grob", "sehr grob"];
const TABS = [
  ["groups", "Gruppen"],
  ["quality", "Qualität"],
  ["singles", "Einzelbilder"],
  ["all", "Alle"],
];
const HINT = "Klick: umdrehen · Rechtsklick: groß · Doppelklick: Vollbild · B: Stapel bestätigen · Strg + Klick: vergleichen · Strg + Z: zurück";

const prefs = { tab: "groups", tileSize: 1, filters: { people: false, changed: false, manual: false, open: false } };

/** Mount the review screen into `main`. `ctx` = {setStatus, setCrumb, setFooter, startScreen, reload}. */
export function mountReview(main, view, ctx) {
  const store = new ReviewStore(view.album_id, view);
  const sheet = h("div.sheet", { tabindex: "-1" });
  const seen = new SeenTracker(store, sheet);
  store.seen = seen;
  const registry = new TileRegistry(seen);
  const xl = createXl(store);
  const selection = new Set();
  let current = null; // {kind, destroy, ids?, bundles?, sheet?}
  let menu = null;
  let lastClick = { id: null, time: 0 };
  let flashTimer = null;
  /** Short message in the status bar, then back to the key hints. */
  function flash(text, ms = 4000) {
    ctx.setStatus(text);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => ctx.setStatus(HINT), ms);
  }

  // ── header tally and footer ─────────────────────────────────────────────────────────
  const tally = document.getElementById("tally");
  const rejectSum = h("span.reject-sum");
  const applyState = view.apply ?? { last: null };
  const lastBatch = applyState.last;
  const undoApplied = h("button.ghost", { type: "button", disabled: !lastBatch,
    title: lastBatch ? `Übernahme vom ${fmt.when(lastBatch.created_at)} rückgängig machen` : "Steht nach dem Übernehmen zur Verfügung",
    onclick: () => undoFlow(store.albumId, lastBatch, ctx.reload) }, "Rückgängig");
  const apply = h("button.primary", { type: "button", onclick: async () => {
    await store.queue; // the last click has to arrive before the summary is computed
    await seen.flush();
    applyFlow(store.albumId, ctx.reload);
  } }, "Übernehmen …");
  ctx.setFooter(rejectSum, h("span.apply-actions", {}, undoApplied, apply));

  // after "Übernehmen" (T2): status line, "Erneut ausdünnen", files that were not moved
  const banner = h("div.applied-banner", { hidden: true });
  function renderBanner() {
    const s = store.stats;
    if (!applyState.applied || !lastBatch) { banner.hidden = true; return; }
    const total = s?.total ?? store.photos.length;
    const before = applyState.count_before;
    banner.hidden = false;
    fill(banner,
      h("span.applied-mark", { "aria-hidden": "true" }),
      h("span.applied-text", {},
        `Übernommen am ${fmt.when(lastBatch.created_at)}`,
        before ? h("span.mono", {}, ` · ${fmt.num(before)} → ${fmt.num(total)} Bilder`) : null,
        s?.hold ? " · Alle übrigen Bilder gelten als behalten." : " · Die Vorschläge beziehen sich auf die verbliebenen Bilder."),
      lastBatch.pending_count
        ? h("button.link", { type: "button", onclick: () => retryFlow(store.albumId, lastBatch, ctx.reload) },
          `${fmt.num(lastBatch.pending_count)} nicht verschoben – erneut versuchen`)
        : null,
      h("span.grow"),
      s?.hold
        ? h("button.ghost.small", { type: "button", title: "Neue Vorschläge für die verbliebenen Bilder – Ihre Entscheidungen bleiben erhalten",
          onclick: async (e) => {
            e.currentTarget.disabled = true;
            try { await api.rethin(store.albumId); ctx.reload(); } catch (err) { flash(err.message, 8000); e.currentTarget.disabled = false; }
          } }, "Erneut ausdünnen")
        : null);
  }

  function renderCounts() {
    const s = store.stats;
    const total = s?.total ?? store.photos.length;
    const keep = s?.keep ?? total;
    const delta = total ? Math.round(((total - keep) / total) * 100) : 0;
    tally.hidden = false;
    tally.replaceChildren(
      h("span", {}, `${fmt.num(total)} Bilder`), h("span.arrow", {}, "→"),
      h("span.after", {}, fmt.num(keep)), h("span.delta", {}, delta ? `−${delta} %` : "±0 %"));
    const { n, bytes } = store.rejectedBytes();
    rejectSum.textContent = `Aussortiert: ${fmt.num(n)} Bilder · ${fmt.bytes(bytes)}`;
    apply.disabled = n === 0;
    apply.title = n ? `${fmt.num(n)} Bilder nach „_Aussortiert“ verschieben – mit Zusammenfassung vorab` : "Keine Bilder aussortiert";
    for (const [key] of TABS) tabCount[key].textContent = fmt.num(tabItems(key).count);
    renderNotice();
  }

  // ── what each tab shows ─────────────────────────────────────────────────────────────
  const passes = (id) => {
    const f = prefs.filters;
    const d = store.decision(id);
    if (f.people && !store.fact(id)?.people) return false;
    if (f.changed && d.keep && !d.review) return false;
    if (f.manual && !d.manual) return false;
    return true;
  };

  function allBundles() {
    const groups = store.groups.filter((g) =>
      g.kind === "bracket" || g.kind === "pano" || g.ids.some((id) => !store.decision(id).keep) || store.isDone(g));
    return [...groups, ...store.copyBundles];
  }

  function visibleGroups() {
    return allBundles()
      .filter((g) => g.ids.some(passes) && !(prefs.filters.open && store.isDone(g)))
      .sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
  }

  function tabItems(key) {
    if (key === "groups") {
      const groups = visibleGroups();
      return { groups, count: groups.length };
    }
    if (key === "quality") {
      const review = [];
      const bad = [];
      for (const p of store.photos) {
        const d = store.decision(p.id);
        if (!passes(p.id)) continue;
        if (d.review) review.push(p.id);
        else if (!d.keep && QUALITY.has(d.code)) bad.push(p.id);
      }
      return { sections: [
        { title: "Bitte prüfen", note: `${fmt.num(review.length)} Bilder – bleiben, wenn Sie nichts ändern · K bestätigt, X sortiert aus`, ids: review },
        { title: "Technisch schwach", note: `${fmt.num(bad.length)} Bilder – Vorschlag: aussortieren`, ids: bad },
      ], count: review.length + bad.length };
    }
    if (key === "singles") {
      const singles = new Set(store.singles);
      const photos = store.photos.filter((p) => singles.has(p.id) && passes(p.id));
      return { sections: daySections(photos), count: photos.length };
    }
    const photos = store.photos.filter((p) => passes(p.id));
    return { sections: daySections(photos), count: photos.length };
  }

  // ── toolbar ─────────────────────────────────────────────────────────────────────────
  const tabCount = {};
  const tabButtons = TABS.map(([key, label]) => {
    tabCount[key] = h("span.tab-count", {}, "0");
    return h("button.tab", { type: "button", role: "tab", dataset: { tab: key }, "aria-selected": String(prefs.tab === key),
      onclick: () => showTab(key) }, label, tabCount[key]);
  });

  const modeButtons = [["clean", "Nur Doppelte & Schlechte"], ["quota", "Zielquote"]].map(([mode, label]) =>
    h("button.mode-opt", { type: "button", role: "radio", dataset: { mode },
      onclick: () => { if (store.stats?.mode !== mode) store.settings({ mode }); } }, label));

  const quotaValue = h("span.quota-value.mono");
  const quotaInput = h("input.quota-range", { type: "range", min: "30", max: "90", step: "5", "aria-label": "Ziel behalten in Prozent" });
  const sendQuota = throttle((v) => store.settings({ keep_target: v / 100 }), 150);
  quotaInput.addEventListener("input", () => {
    quotaValue.textContent = `${quotaInput.value} %`;
    sendQuota(Number(quotaInput.value));
  });
  const quota = h("label.quota", {}, h("span.strength-label", {}, "Ziel behalten"), quotaInput, quotaValue);

  const notches = LEVEL_NAMES.map((name, i) => h("button.notch", { type: "button", title: `Gruppierung: ${name}`,
    "aria-label": `Gruppierung ${name}`, onclick: () => {
      if (store.stats?.mode === "quota" && store.stats.level !== i) store.settings({ level: i });
    } }));
  const strength = h("div.strength", { role: "group", "aria-label": "Gruppierungsstärke" },
    h("span.strength-label", {}, "Gruppierung"), h("span.notches", {}, notches), h("span.strength-ends", {}, "fein … grob"));

  function renderSettings() {
    const s = store.stats;
    if (!s) return;
    const held = Boolean(s.hold);
    for (const b of modeButtons) {
      b.setAttribute("aria-checked", String(b.dataset.mode === s.mode));
      b.disabled = held;
      b.title = held ? "Album ist übernommen – für neue Vorschläge „Erneut ausdünnen“ wählen" : "";
    }
    quotaInput.disabled = held;
    quota.hidden = s.mode !== "quota";
    if (document.activeElement !== quotaInput) {
      quotaInput.value = String(Math.round(s.keep_target * 100));
      quotaValue.textContent = `${quotaInput.value} %`;
    }
    const clean = s.mode !== "quota" || held;
    strength.classList.toggle("is-disabled", clean);
    strength.title = clean ? "Im Modus „Nur Doppelte & Schlechte“ werden Motivgruppen nicht ausgedünnt – die Gruppierung wirkt nur in „Zielquote“." : "";
    notches.forEach((b, i) => {
      b.classList.toggle("on", i === s.level);
      b.setAttribute("aria-pressed", String(i === s.level));
      b.disabled = clean;
    });
  }

  const filterBoxes = [["people", "nur mit Personen"], ["changed", "nur geänderte"], ["manual", "nur von Hand geändert"], ["open", "nur offene Stapel"]].map(([key, label]) => {
    const input = h("input", { type: "checkbox", id: `f-${key}`, checked: prefs.filters[key],
      onchange: (e) => { prefs.filters[key] = e.target.checked; showTab(prefs.tab); } });
    return h("label.check.small", { for: `f-${key}` }, input, h("span.box", { "aria-hidden": "true" }), label);
  });

  const doneBar = h("span.done-fill");
  const doneText = h("span.mono");
  const doneCount = h("span.done-count", { hidden: true }, h("span.done-track", { "aria-hidden": "true" }, doneBar), doneText);
  function renderDone() {
    const all = prefs.tab === "groups" ? allBundles() : [];
    const n = all.filter((g) => store.isDone(g)).length;
    doneCount.hidden = !all.length;
    doneText.textContent = `${fmt.num(n)} von ${fmt.num(all.length)} Stapeln erledigt`;
    doneBar.style.transform = `scaleX(${all.length ? n / all.length : 0})`;
    doneCount.classList.toggle("is-complete", all.length > 0 && n === all.length);
  }

  /** "Stapel bestätigen": mark it, close it and step into the next open one. */
  function confirmGroup(g) {
    const bundles = current?.bundles;
    if (!bundles) return;
    store.setDone(g, true);
    const next = bundles.nextOpen(g);
    if (next) {
      bundles.toggle(next.g, true);
    } else {
      bundles.toggle(g);
      flash("Alle Stapel bestätigt – weiter mit dem Reiter „Qualität“ oder „Übernehmen …“.", 10000);
    }
  }

  const notice = h("p.notice", { hidden: true });
  function renderNotice() {
    const s = store.stats;
    if (!s || s.mode !== "quota") { notice.hidden = true; return; }
    const floor = s.keep_floor ?? 0;
    const tooLow = floor > s.keep_target + 0.005;
    if (!tooLow && !s.low_singles) { notice.hidden = true; return; }
    const box = h("input", { type: "checkbox", id: "low-singles", checked: s.low_singles,
      onchange: (e) => store.settings({ low_singles: e.target.checked }) });
    notice.hidden = false;
    fill(notice, 
      tooLow ? h("span.mark", { "aria-hidden": "true" }, "!") : null,
      tooLow ? `Mit den Schutzregeln sind höchstens ${Math.round((1 - floor) * 100)} % Reduktion möglich.` : null,
      h("label.check.small", { for: "low-singles" }, box, h("span.box", { "aria-hidden": "true" }),
        "Auch Einzelbilder mit geringem Wert aussortieren"));
  }

  const selectionBar = h("span.selection", { hidden: true });
  function renderSelection() {
    selectionBar.hidden = selection.size === 0;
    selectionBar.replaceChildren(
      h("span", {}, `${selection.size} ausgewählt`),
      h("button.ghost.small", { type: "button", disabled: selection.size < 2 || selection.size > 4, onclick: compare,
        title: "2–4 Bilder nebeneinander" }, "Vergleichen", h("kbd", {}, "C")),
      h("button.link", { type: "button", onclick: clearSelection }, "Auswahl aufheben"));
    for (const t of sheet.querySelectorAll(".tile.is-selected")) {
      if (!selection.has(Number(t.dataset.id))) t.classList.remove("is-selected");
    }
    for (const id of selection) for (const t of sheet.querySelectorAll(`.tile[data-id="${id}"]`)) t.classList.add("is-selected");
  }
  function clearSelection() { selection.clear(); renderSelection(); }

  const sizeButtons = TILE_SIZES.map((px, i) => h("button.seg", { type: "button", title: `Kacheln ${px} px (Strg + Mausrad)`,
    onclick: () => setSize(i) }, ["S", "M", "L"][i]));
  function setSize(i) {
    prefs.tileSize = Math.max(0, Math.min(TILE_SIZES.length - 1, i));
    main.style.setProperty("--tile", `${TILE_SIZES[prefs.tileSize]}px`);
    sizeButtons.forEach((b, j) => b.setAttribute("aria-pressed", String(j === prefs.tileSize)));
  }

  main.replaceChildren(
    h("div.toolbar", {},
      h("div.tabs", { role: "tablist" }, tabButtons),
      h("div.mode", { role: "radiogroup", "aria-label": "Modus" }, modeButtons),
      quota, strength),
    h("div.subbar", {},
      h("div.filters", {}, filterBoxes),
      doneCount, selectionBar, notice,
      h("span.grow"),
      h("div.segmented", { role: "group", "aria-label": "Kachelgröße" }, sizeButtons),
      h("button.ghost.small", { type: "button", onclick: ctx.startScreen }, "Anderes Album")),
    banner,
    sheet);
  main.className = "review";
  setSize(prefs.tileSize);

  // ── tabs ────────────────────────────────────────────────────────────────────────────
  function showTab(key) {
    prefs.tab = key;
    tabButtons.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === key)));
    current?.destroy?.();
    registry.clear();
    closeMenu();
    xl.close();
    const items = tabItems(key);
    const total = store.photos.length;
    sheet.scrollTop = 0;
    if (key === "groups") {
      if (!items.groups.length) {
        sheet.replaceChildren(h("p.empty", {}, store.stats ? emptyGroupsText() : "Noch keine Analyse – keine Gruppen."));
        current = { kind: "empty" };
      } else {
        const bundles = renderBundles(store, items.groups, { registry, groupMenu, confirm: confirmGroup, onToggle: () => xl.close() });
        sheet.replaceChildren(bundles.el);
        current = { kind: "bundles", bundles, destroy: bundles.destroy };
        sheet.querySelector(".bundle")?.focus({ preventScroll: true });
      }
    } else {
      if (!items.count) {
        sheet.replaceChildren(h("p.empty", {}, emptyText(key)));
        current = { kind: "empty" };
      } else {
        const ids = items.sections.flatMap((s) => s.ids);
        const grid = renderSheet(sheet, items.sections, (id) => registry.add(decisionTile(store, id, total)),
          (index) => openPhoto(ids, index));
        current = { kind: "grid", grid, ids, destroy: grid.destroy };
        grid.focus(0);
      }
    }
    renderSelection();
    renderCounts();
    renderDone();
  }

  function emptyGroupsText() {
    if (store.stats.hold) return "Album übernommen – alle verbliebenen Bilder gelten als behalten. „Erneut ausdünnen“ macht neue Vorschläge.";
    return store.stats.mode === "clean"
      ? "Keine Serien mit fast gleichen Bildern zum Ausdünnen. Ähnliche Motive bleiben in diesem Modus unangetastet – alle Bilder unter „Alle“."
      : prefs.filters.open ? "Alle Stapel sind bestätigt." : "Keine Gruppe mit aussortierten Bildern.";
  }
  function emptyText(key) {
    if (Object.values(prefs.filters).some(Boolean)) return "Kein Bild passt zum Filter.";
    return { quality: "Keine technisch schwachen Bilder gefunden.", singles: "Keine Einzelbilder.", all: "Keine Bilder in diesem Album." }[key];
  }

  // ── opening photos ──────────────────────────────────────────────────────────────────
  function openPhoto(ids, index) {
    const photos = ids.map((id) => store.byId.get(id)).filter(Boolean);
    if (!photos.length) return;
    closeMenu();
    xl.close();
    openViewer({ albumId: store.albumId, photos, index, store, onClose: (id) => focusId(id) });
  }

  function focusId(id) {
    const t = sheet.querySelector(`.tile[data-id="${id}"]`);
    if (!t) return;
    if (current?.kind === "grid" && t.dataset.index) current.grid.focus(Number(t.dataset.index));
    else t.focus();
  }

  function contextIds(tile) {
    if (current?.kind === "bundles") return current.bundles.bandIds(tile);
    return current?.ids ?? [];
  }

  function openFromTile(tile) {
    const ids = contextIds(tile);
    const id = Number(tile.dataset.id);
    openPhoto(ids, Math.max(0, ids.indexOf(id)));
  }

  function compare() {
    if (selection.size < 2 || selection.size > 4) return;
    const last = document.activeElement;
    openCompare({ store, ids: [...selection], onClose: () => last?.focus?.() });
  }

  // ── menus ───────────────────────────────────────────────────────────────────────────
  function closeMenu() {
    menu?.remove();
    menu = null;
  }
  function openMenu(x, y, items) {
    closeMenu();
    menu = h("div.menu", { role: "menu" }, items.filter(Boolean).map((it) =>
      h("button.menu-item", { type: "button", role: "menuitem", disabled: it.disabled,
        onclick: () => { closeMenu(); it.action(); } }, h("span", {}, it.label), it.kbd ? h("kbd", {}, it.kbd) : null)));
    document.body.append(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`;
    menu.style.top = `${Math.min(y, innerHeight - r.height - 8)}px`;
    menu.querySelector("button:not(:disabled)")?.focus();
  }
  function tileMenu(tile, x, y) {
    const id = Number(tile.dataset.id);
    const d = store.decision(id);
    openMenu(x, y, [
      { label: d.review ? "Geprüft – behalten" : "Behalten", kbd: "K", disabled: d.unanalysed || (d.keep && !d.review), action: () => store.setKeep([id], true) },
      { label: "Aussortieren", kbd: "X", disabled: d.unanalysed || !d.keep, action: () => store.setKeep([id], false) },
      { label: "Vorschlag wiederherstellen", disabled: !d.manual, action: () => store.setKeep([id], null) },
      { label: "Großansicht", kbd: "Enter", action: () => openFromTile(tile) },
      { label: selection.has(id) ? "Aus dem Vergleich nehmen" : "Zum Vergleich wählen", kbd: "Strg + Klick", action: () => toggleSelect(id) },
    ]);
  }
  function groupMenu(g, anchor) {
    const r = anchor.getBoundingClientRect();
    const auto = g.ids.filter((id) => store.decision(id).manual);
    openMenu(r.left, r.bottom + 4, [
      { label: "Alle behalten", disabled: g.ids.every((id) => store.decision(id).keep && !store.decision(id).review), action: () => store.setKeep(g.ids, true) },
      { label: "Vorschlag wiederherstellen", disabled: !auto.length, action: () => store.setKeep(g.ids, null) },
      { label: "Alle im Vergleich (bis 4)", disabled: g.ids.length < 2, action: () => {
        selection.clear();
        for (const id of g.ids.slice(0, 4)) selection.add(id);
        renderSelection();
        compare();
      } },
    ]);
  }
  document.addEventListener("pointerdown", onDocPointer, true);
  function onDocPointer(e) { if (menu && !menu.contains(e.target)) closeMenu(); }

  function toggleSelect(id) {
    if (selection.has(id)) selection.delete(id);
    else if (selection.size < 4) selection.add(id);
    renderSelection();
  }

  // ── pointer interactions ────────────────────────────────────────────────────────────
  sheet.addEventListener("click", (e) => {
    const tile = e.target.closest(".tile.decided");
    if (!tile || e.detail === 0) return; // keyboard "clicks" are handled in keydown
    const id = Number(tile.dataset.id);
    if (e.ctrlKey || e.metaKey) { toggleSelect(id); return; }
    if (e.detail > 1) return;
    if (xl.isOpen) { xl.close(); lastClick = { id: null, time: 0 }; return; } // the click only closes XL
    lastClick = { id, time: performance.now() };
    store.toggle(id);
  });
  sheet.addEventListener("dblclick", (e) => {
    const tile = e.target.closest(".tile.decided");
    if (!tile) return;
    const id = Number(tile.dataset.id);
    if (lastClick.id === id && performance.now() - lastClick.time < 700) store.undo(); // the first click of the double click
    openFromTile(tile);
  });
  sheet.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const tile = e.target.closest(".tile.decided");
    if (menu || !tile) { if (!tile) xl.close(); return; }
    const same = xl.id === Number(tile.dataset.id);
    tile.focus({ preventScroll: true });
    if (same) xl.close(); else xl.open(tile);
  });
  sheet.addEventListener("mouseover", (e) => {
    const tile = e.target.closest(".tile");
    for (const t of sheet.querySelectorAll(".tile.is-ref")) t.classList.remove("is-ref");
    const ref = tile?.dataset.ref;
    if (ref && tile.classList.contains("is-reject")) {
      for (const t of sheet.querySelectorAll(`.tile[data-id="${ref}"]`)) t.classList.add("is-ref");
    }
  });
  sheet.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setSize(prefs.tileSize + (e.deltaY < 0 ? 1 : -1));
  }, { passive: false });

  // ── keyboard ────────────────────────────────────────────────────────────────────────
  function geoMove(from, key) {
    const items = [...sheet.querySelectorAll(".bundle, .bundle-open .tile")];
    const i = items.indexOf(from);
    if (i < 0) return items[0];
    if (key === "ArrowRight") return items[i + 1];
    if (key === "ArrowLeft") return items[i - 1];
    const r = from.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const down = key === "ArrowDown";
    let best = null;
    let bestScore = Infinity;
    for (const el of items) {
      if (el === from) continue;
      const q = el.getBoundingClientRect();
      const dy = down ? q.top - r.bottom : r.top - q.bottom;
      if (dy < -2) continue;
      const score = dy * 4 + Math.abs(q.left + q.width / 2 - cx);
      if (score < bestScore) { bestScore = score; best = el; }
    }
    return best;
  }

  function focusEl(el) {
    if (!el) return;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: "nearest" });
  }

  sheet.addEventListener("keydown", (e) => {
    if (e.altKey || e.metaKey) return;
    const tile = document.activeElement?.closest?.(".tile.decided");
    const bundle = document.activeElement?.closest?.(".bundle");
    const id = tile ? Number(tile.dataset.id) : null;
    if (e.ctrlKey) return;
    const k = e.key;
    if (tile && (k === " " || k === "Spacebar")) { e.preventDefault(); store.toggle(id); return; }
    if (tile && (k === "k" || k === "K")) { e.preventDefault(); store.setKeep([id], true); return; }
    if (tile && (k === "x" || k === "X")) { e.preventDefault(); store.setKeep([id], false); return; }
    if (k === "c" || k === "C") { e.preventDefault(); compare(); return; }
    if (k === "Escape" && xl.isOpen) { e.preventDefault(); xl.close(); return; }
    if (k === "b" || k === "B") {
      const g = current?.bundles?.openGroup();
      if (g && !store.isDone(g)) { e.preventDefault(); confirmGroup(g); }
      return;
    }
    if (tile && k === "ContextMenu") { e.preventDefault(); const r = tile.getBoundingClientRect(); tileMenu(tile, r.left + 20, r.top + 20); return; }
    if (current?.kind !== "bundles") return; // grid tabs: arrows, Enter in grid.js
    if (tile && k === "Enter") { e.preventDefault(); openFromTile(tile); return; }
    if (k === "Escape") {
      const el = current.bundles.closeFocused();
      if (el) { e.preventDefault(); focusEl(el); }
      return;
    }
    if (["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(k)) {
      e.preventDefault();
      focusEl(geoMove(tile ?? bundle ?? sheet.querySelector(".bundle"), k));
      return;
    }
    if (k === "PageDown" || k === "PageUp") {
      e.preventDefault();
      const all = [...sheet.querySelectorAll(".bundle")];
      const from = bundle ?? tile?.closest(".bundle-open")?.previousElementSibling;
      let i = all.indexOf(bundle ?? all.find((b) => b.getAttribute("aria-expanded") === "true" && tile?.closest(".bundle-open")?.dataset.group === b.dataset.group));
      if (i < 0) i = from ? all.indexOf(from) : 0;
      focusEl(all[Math.max(0, Math.min(all.length - 1, i + (k === "PageDown" ? 1 : -1)))]);
      return;
    }
    if (bundle && (k === "Enter" || k === " ") && !e.repeat) {
      e.preventDefault(); // no native button click: open/close here and step into the band
      const entry = current.bundles.bundleOf(bundle);
      if (entry) current.bundles.toggle(entry.g, true);
    }
  });

  function onGlobalKey(e) {
    if (document.querySelector(".viewer")) return; // overlays handle their own keys
    if (e.ctrlKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault();
      if (!store.undo()) flash("Nichts zum Zurücknehmen.");
    } else if (e.key === "Escape" && (menu || xl.isOpen)) {
      closeMenu();
      xl.close();
    }
  }
  document.addEventListener("keydown", onGlobalKey);

  // ── store events ────────────────────────────────────────────────────────────────────
  const unsubscribe = store.subscribe((event) => {
    if (event.type === "error") {
      flash(`Änderung nicht gespeichert: ${event.error.message}`, 8000);
      return;
    }
    if (event.type === "done") {
      current?.bundles?.refreshGroup(event.group);
      renderDone();
      return;
    }
    renderSettings();
    renderBanner();
    xl.render();
    if (event.structure) {
      showTab(prefs.tab);
      return;
    }
    registry.update(store, event.ids);
    current?.bundles?.refresh(event.ids);
    renderCounts();
    renderDone();
  });

  ctx.setStatus(HINT);
  renderSettings();
  renderBanner();
  showTab(prefs.tab);
  if (!view.review) ctx.setStatus("Noch nicht analysiert – Vorschläge erscheinen nach der Analyse.");

  return {
    destroy() {
      unsubscribe();
      seen.destroy();
      xl.close();
      clearTimeout(flashTimer);
      current?.destroy?.();
      document.removeEventListener("keydown", onGlobalKey);
      document.removeEventListener("pointerdown", onDocPointer, true);
      closeMenu();
      tally.hidden = true;
    },
  };
}

