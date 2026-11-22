// FotoDünner frontend: start → pre-check → analysis → contact sheet (04 §1).
import { api, ApiError } from "./api.js";
import { renderSheet, TILE_SIZES, tile } from "./grid.js";
import { cropMarks, fmt, h } from "./util.js";
import { openViewer } from "./viewer.js";

const main = document.getElementById("app");
const crumb = document.getElementById("crumb");
const statusText = document.getElementById("status-text");

const LIVE_TILES = 96; // newest previews shown while analysing
const STAGE_LABEL = { scan: "Ordner durchsuchen", analyze: "Bilder einlesen und prüfen" };

const ui = {
  recursive: true,
  tileSize: 1, // index into TILE_SIZES
  sheet: null,
  events: null,
};

function setCrumb(text) {
  crumb.textContent = text ?? "";
  crumb.hidden = !text;
}

function setStatus(text) {
  statusText.textContent = text ?? "";
}

function show(...nodes) {
  ui.sheet?.destroy();
  ui.sheet = null;
  ui.events?.close();
  ui.events = null;
  main.className = "stage";
  main.replaceChildren(...nodes);
}

function errorLine() {
  return h("p.error", { role: "alert", hidden: true });
}

function showError(el, err) {
  el.hidden = false;
  el.textContent = err instanceof ApiError || err instanceof Error ? err.message : String(err);
}

function panel(cls, ...children) {
  return h(`section.panel${cls ? `.${cls}` : ""}`, {}, ...cropMarks(), ...children);
}

function readout(label, value, title) {
  return h("div.readout-cell", { title }, h("span.readout-label", {}, label), h("span.readout-value", {}, value));
}

// ── 1.1 Start ─────────────────────────────────────────────────────────────────────

async function startScreen() {
  setCrumb(null);
  setStatus("Bereit");
  const err = errorLine();
  const pathInput = h("input.path-input", {
    type: "text", placeholder: "C:\\Users\\…\\Bilder\\Urlaub 2025", "aria-label": "Ordnerpfad",
    spellcheck: "false",
  });
  const pathForm = h("form.path-form", {
    hidden: true,
    onsubmit: (e) => { e.preventDefault(); openAlbum(pathInput.value.trim(), err); },
  }, pathInput, h("button.ghost", { type: "submit" }, "Öffnen"));

  const pick = h("button.primary", {
    type: "button",
    onclick: async () => {
      err.hidden = true;
      try {
        const { path } = await api.pickFolder();
        if (path) await openAlbum(path, err);
      } catch (e) {
        if (e.status === 501) { pathForm.hidden = false; pathInput.focus(); } else showError(err, e);
      }
    },
  }, "Ordner wählen …");

  const recursive = h("input", { type: "checkbox", id: "recursive", checked: ui.recursive,
    onchange: (e) => { ui.recursive = e.target.checked; } });

  const recentBox = h("section.recent", { "aria-labelledby": "recent-title", hidden: true });
  const hint = h("p.hint", { hidden: true }, h("span.mark", { "aria-hidden": "true" }, "!"),
    "Tipp: Testen Sie FotoDünner zuerst mit einer Kopie eines Urlaubsordners.");

  show(
    panel("",
      h("p.eyebrow", {}, "Neues Album"),
      h("h1", {}, "Urlaubsalbum ausdünnen"),
      h("p.lead", {},
        "FotoDünner findet Doppelte, Serienbilder und missglückte Aufnahmen und schlägt vor, was weg kann. ",
        "Sie prüfen jeden Vorschlag selbst. Gelöscht wird nichts – aussortierte Bilder landen im Unterordner ",
        h("code", {}, "_Aussortiert"), " und lassen sich jederzeit zurückholen."),
      h("div.actions", {}, pick,
        h("label.check", { for: "recursive" }, recursive, h("span.box", { "aria-hidden": "true" }), "Unterordner einbeziehen")),
      pathForm,
      err,
      h("ol.steps", {},
        h("li", {}, h("span.num", {}, "01"), "Ordner wählen"),
        h("li", {}, h("span.num", {}, "02"), "Analysieren"),
        h("li", {}, h("span.num", {}, "03"), "Prüfen & übernehmen"))),
    recentBox,
    hint,
  );
  pick.focus();

  try {
    const items = await api.recent();
    if (!items.length) { hint.hidden = false; return; }
    recentBox.hidden = false;
    recentBox.append(
      h("h2.section-title", { id: "recent-title" }, "Zuletzt geöffnet"),
      h("ul.recent-list", {}, items.map((item) => h("li", {},
        h("button.recent-item", {
          type: "button",
          title: item.path,
          onclick: () => { ui.recursive = item.recursive; openAlbum(item.path, err); },
        },
        h("span.recent-name", {}, item.name),
        h("span.recent-path", {}, item.path),
        h("span.recent-count", {}, `${fmt.num(item.photos)} Bilder`),
        h("span.recent-state", {}, item.done >= item.photos ? "analysiert"
          : `${fmt.num(item.done)} von ${fmt.num(item.photos)} analysiert`))))),
    );
  } catch {
    hint.hidden = false;
  }
}

async function openAlbum(path, err) {
  if (!path) return;
  setStatus("Ordner wird geprüft …");
  main.classList.add("is-busy");
  try {
    const info = await api.open(path, ui.recursive);
    precheckScreen(info);
  } catch (e) {
    main.classList.remove("is-busy");
    setStatus("Bereit");
    showError(err, e);
  }
}

// ── 1.2 Vorprüfung ────────────────────────────────────────────────────────────────

function precheckScreen(info) {
  setCrumb(info.name);
  setStatus(`${fmt.num(info.photos)} JPEG-Bilder gefunden`);
  const err = errorLine();
  const warnings = [];
  if (info.photos === 0) warnings.push("In diesem Ordner wurden keine JPEG-Bilder gefunden.");
  if (info.offline) {
    warnings.push(`${fmt.num(info.offline)} Bilder liegen nur in der Cloud (OneDrive). Beim Lesen würden sie heruntergeladen.`);
  }
  if (info.drive === "external") warnings.push("Das Album liegt auf einem externen Laufwerk. Die Analyse dauert dort länger – bitte das Laufwerk nicht trennen.");
  if (info.drive === "network") warnings.push("Das Album liegt auf einem Netzlaufwerk. Die Analyse dauert dort deutlich länger.");
  if (info.few && info.photos > 0) warnings.push(`Nur ${fmt.num(info.photos)} Bilder – FotoDünner lohnt sich vor allem bei großen Alben.`);
  if (info.unreadable_dirs.length) warnings.push(`${fmt.num(info.unreadable_dirs.length)} Unterordner konnten nicht gelesen werden.`);

  const skipOffline = h("input", { type: "checkbox", id: "skip-offline", checked: true });
  const done = info.pending === 0;
  const resume = info.cached > 0 && !done;
  const start = h("button.primary", {
    type: "button",
    disabled: info.photos === 0,
    onclick: async () => {
      if (done) return reviewScreen(info.album_id);
      try {
        await api.analyze(info.album_id, info.offline ? skipOffline.checked : false);
        progressScreen(info);
      } catch (e) { showError(err, e); }
    },
  }, done ? "Übersicht öffnen" : resume ? "Analyse fortsetzen" : "Analyse starten");

  show(
    panel("wide",
      h("p.eyebrow", {}, "Vorprüfung"),
      h("h1", {}, info.name),
      h("p.path", {}, info.path),
      h("div.readouts", {},
        readout("JPEG-Bilder", fmt.num(info.photos)),
        readout("Gesamtgröße", fmt.bytes(info.total_bytes)),
        readout("Ignoriert", fmt.num(info.ignored), "Andere Formate, Videos usw. – werden nie verschoben"),
        readout("Schon analysiert", fmt.num(info.cached)),
        readout("Geschätzte Dauer", done ? "sofort" : `≈ ${fmt.duration(info.estimate_seconds)}`)),
      warnings.length
        ? h("ul.warnings", {}, warnings.map((w) => h("li", {}, h("span.mark", { "aria-hidden": "true" }, "!"), w)))
        : null,
      info.offline
        ? h("label.check", { for: "skip-offline" }, skipOffline, h("span.box", { "aria-hidden": "true" }),
          "Cloud-Bilder überspringen (nichts herunterladen)")
        : null,
      h("div.actions", {}, start,
        h("button.ghost", { type: "button", onclick: startScreen }, "Anderen Ordner wählen")),
      err),
  );
  start.focus();
}

// ── 1.3 Analyse läuft ─────────────────────────────────────────────────────────────

function scale() {
  return h("div.scale", { "aria-hidden": "true" },
    Array.from({ length: 21 }, (_, i) => h(`span.tick${i % 5 === 0 ? ".major" : ""}`)));
}

function progressScreen(info) {
  setCrumb(info.name);
  const albumId = info.album_id;
  const fill = h("span.meter-fill");
  const counter = h("span.counter", {}, "–");
  const percent = h("span.percent", {}, "0 %");
  const stage = h("span.stage-label", {}, STAGE_LABEL.scan);
  const eta = h("span.mono", {}, "–");
  const current = h("span.mono.current", {}, " ");
  const live = h("div.live-grid", { "aria-label": "Zuletzt analysierte Bilder" });
  const err = errorLine();
  const cancel = h("button.ghost", {
    type: "button",
    onclick: async () => {
      cancel.disabled = true;
      cancel.textContent = "Wird angehalten …";
      try { await api.cancel(albumId); } catch (e) { showError(err, e); }
    },
  }, "Abbrechen");
  const meter = h("div.meter", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" },
    h("span.meter-track", {}, fill), scale());

  show(
    panel("wide",
      h("p.eyebrow", {}, "Analyse"),
      h("h1", {}, info.name),
      h("div.progress-head", {}, stage, h("span.progress-numbers", {}, counter, percent)),
      meter,
      h("dl.progress-facts", {},
        h("dt", {}, "Restzeit"), h("dd", {}, eta),
        h("dt", {}, "Aktuell"), h("dd", {}, current)),
      h("div.actions", {}, cancel,
        h("span.note", {}, "Abbrechen ist jederzeit möglich – beim nächsten Öffnen geht es weiter.")),
      err),
    live,
  );
  setStatus("Analyse läuft – Sie können den Laptop weiter benutzen");

  let total = 0;
  const source = api.events(albumId);
  ui.events = source;
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const p = data.progress;
    if (p) {
      stage.textContent = STAGE_LABEL[p.stage] ?? stage.textContent;
      total = p.total || total;
      const ratio = p.total ? p.done / p.total : 0;
      fill.style.transform = `scaleX(${ratio})`;
      meter.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
      counter.textContent = p.total ? `${fmt.frame(p.done, p.total)} / ${fmt.num(p.total)}` : "–";
      percent.textContent = `${Math.floor(ratio * 100)} %`;
      eta.textContent = p.eta_seconds ? `≈ ${fmt.duration(p.eta_seconds)}` : p.stage === "analyze" ? "wird berechnet …" : "–";
      current.textContent = p.current_file ?? " ";
    }
    for (const id of data.finished) {
      live.prepend(tile(albumId, { id, n: 0, name: "", status: "done", v: 0 }, 0));
      while (live.childElementCount > LIVE_TILES) live.lastElementChild.remove();
    }
    if (data.state === "running") return;
    source.close();
    ui.events = null;
    if (data.state === "done") reviewScreen(albumId);
    else if (data.state === "cancelled") cancelledScreen(info, p);
    else showError(err, data.error || "Die Analyse ist fehlgeschlagen.");
  };
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) showError(err, "Verbindung zum Programm verloren.");
  };
}

function cancelledScreen(info, progress) {
  setStatus("Analyse angehalten");
  const done = (progress?.done ?? 0) + info.cached;
  show(
    panel("",
      h("p.eyebrow", {}, "Angehalten"),
      h("h1", {}, info.name),
      h("p.lead", {}, `${fmt.num(done)} von ${fmt.num(info.photos)} Bildern sind analysiert und gespeichert. `,
        "Beim nächsten Öffnen geht es an dieser Stelle weiter."),
      h("div.actions", {},
        h("button.primary", { type: "button", onclick: () => openAlbum(info.path, errorLine()) }, "Fortsetzen"),
        h("button.ghost", { type: "button", onclick: () => reviewScreen(info.album_id) }, "Bisherige Bilder ansehen"),
        h("button.ghost", { type: "button", onclick: startScreen }, "Startseite"))),
  );
}

// ── 1.4 Übersicht („Alle“, M2 ohne Entscheidungen) ───────────────────────────────

async function reviewScreen(albumId) {
  setStatus("Übersicht wird geladen …");
  let view;
  try {
    view = await api.view(albumId);
  } catch (e) {
    const err = errorLine();
    show(panel("", h("h1", {}, "Übersicht"), err, h("button.ghost", { type: "button", onclick: startScreen }, "Startseite")));
    showError(err, e);
    return;
  }
  setCrumb(view.name);
  const photos = view.photos;
  const unreadable = photos.filter((p) => p.status === "error").length;
  const pending = photos.filter((p) => p.status === "pending").length;

  const sheet = h("div.sheet", { tabindex: "-1" });
  const sizeButtons = TILE_SIZES.map((px, i) => h("button.seg", {
    type: "button", "aria-pressed": String(i === ui.tileSize), title: `Kacheln ${px} px (Strg + Mausrad)`,
    onclick: () => setSize(i),
  }, ["S", "M", "L"][i]));

  function setSize(i) {
    ui.tileSize = Math.max(0, Math.min(TILE_SIZES.length - 1, i));
    main.style.setProperty("--tile", `${TILE_SIZES[ui.tileSize]}px`);
    sizeButtons.forEach((b, j) => b.setAttribute("aria-pressed", String(j === ui.tileSize)));
  }

  show(
    h("div.toolbar", {},
      h("div.tabs", { role: "tablist" },
        h("button.tab", { type: "button", role: "tab", "aria-selected": "true" }, "Alle", h("span.tab-count", {}, fmt.num(photos.length)))),
      h("div.toolbar-facts", {},
        unreadable ? h("span.fact.warn", { title: "Werden nie automatisch aussortiert" }, `${fmt.num(unreadable)} nicht lesbar`) : null,
        pending ? h("span.fact", {}, `${fmt.num(pending)} noch nicht analysiert`) : null),
      h("div.segmented", { role: "group", "aria-label": "Kachelgröße" }, sizeButtons),
      h("button.ghost", { type: "button", onclick: startScreen }, "Anderes Album")),
    sheet,
  );
  main.className = "review";
  setSize(ui.tileSize);
  setStatus("Doppelklick oder Enter: Großansicht · Pfeiltasten: bewegen · Strg + Mausrad: Kachelgröße");

  if (!photos.length) {
    sheet.append(h("p.empty", {}, "Keine Bilder in diesem Album."));
    return;
  }
  ui.sheet = renderSheet(sheet, albumId, photos, (index) => {
    openViewer({ albumId, photos, index, onClose: (last) => ui.sheet?.focus(last) });
  });
  ui.sheet.focus(0);

  sheet.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    setSize(ui.tileSize + (event.deltaY < 0 ? 1 : -1));
  }, { passive: false });
}

// ── boot ──────────────────────────────────────────────────────────────────────────

api.version().then((v) => { document.getElementById("version").textContent = `v${v.version}`; }).catch(() => {});
startScreen();
