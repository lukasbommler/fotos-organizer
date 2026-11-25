// FotoDünner frontend: start → pre-check → analysis → contact sheet (04 §1).
import { api, ApiError } from "./api.js";
import { initDesign, samplePhoto, stage } from "./design.js";
import { undoFlow } from "./dialogs.js";
import { mountReview } from "./review.js";
import { cropMarks, fmt, h, reducedMotion } from "./util.js";

const main = document.getElementById("app");
const crumb = document.getElementById("crumb");
const statusText = document.getElementById("status-text");

const STAGE_LABEL = {
  scan: "Ordner durchsuchen",
  analyze: "Bilder einlesen und prüfen",
  faces: "Gesichter in Serien genau prüfen",
};

const ui = {
  recursive: true,
  review: null,
  events: null,
};

const footerLeft = document.getElementById("footer-left");
const footerRight = document.getElementById("footer-right");
const footerDefault = [footerLeft.textContent, footerRight.textContent];
const footerBar = footerLeft.parentElement;

/** Review screen: sorted-out total left, apply buttons right; other screens: defaults. */
function setFooter(left, right) {
  footerBar.classList.toggle("applybar", Boolean(left));
  footerLeft.replaceChildren(left ?? footerDefault[0]);
  footerRight.replaceChildren(right ?? footerDefault[1]);
}

function setCrumb(text) {
  crumb.textContent = text ?? "";
  crumb.hidden = !text;
}

function setStatus(text) {
  statusText.textContent = text ?? "";
}

function show(...nodes) {
  ui.review?.destroy();
  ui.review = null;
  setFooter(null, null);
  ui.events?.close();
  ui.events = null;
  clearInterval(ui.timer);
  clearInterval(ui.demo);
  stage.stop();
  main.className = "stage";
  main.replaceChildren(...nodes);
}

/** Screen on the animated stage: text left, the core in the middle, facts right (designs.css). */
function showHud(kind, { title, core, side = [], foot = [] }, mode, extra = {}) {
  show(h("div.hud-title", {}, title), h("div.hud-core", {}, core), h("aside.hud-side", {}, side), h("div.hud-foot", {}, foot));
  main.className = `hud hud-${kind}`;
  stage.set({ mode, done: 0, total: 0, ...extra });
  stage.start();
}

const DONE_MS = 1300; // closing flash of the animation before the review opens

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

  showHud("start", {
    title: [
      h("p.eyebrow", {}, "Neues Album"),
      h("h1", {}, "Urlaubsalbum ausdünnen"),
      h("p.lead", {},
        "FotoDünner findet Doppelte, Serienbilder und missglückte Aufnahmen und schlägt vor, was weg kann. ",
        "Sie prüfen jeden Vorschlag selbst. Gelöscht wird nichts – aussortierte Bilder landen im Unterordner ",
        h("code", {}, "_Aussortiert"), " und lassen sich jederzeit zurückholen."),
      pathForm, err],
    core: [pick, h("label.check", { for: "recursive" }, recursive, h("span.box", { "aria-hidden": "true" }), "Unterordner einbeziehen")],
    side: [recentBox, hint],
    foot: [h("ol.steps", {},
      h("li", {}, h("span.num", {}, "01"), "Ordner wählen"),
      h("li", {}, h("span.num", {}, "02"), "Analysieren"),
      h("li", {}, h("span.num", {}, "03"), "Prüfen & übernehmen")),
    h("button.link.demo-link", { type: "button", onclick: demoScreen }, "Analyse-Animation ansehen")],
  }, "idle", { reset: true });
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
        h("span.recent-state", {}, item.applied ? "übernommen" : item.done >= item.photos ? "analysiert"
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
  const since = performance.now();
  stage.set({ mode: "scan" });
  try {
    const info = await api.open(path, ui.recursive);
    // let the reading animation be seen, but never make anybody wait for it
    if (!reducedMotion()) await new Promise((r) => setTimeout(r, Math.max(0, 900 - (performance.now() - since))));
    precheckScreen(info);
  } catch (e) {
    main.classList.remove("is-busy");
    stage.set({ mode: "idle" });
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

  const last = info.apply?.last;
  const applied = last
    ? h("div.applied-note", {},
      h("p", {}, h("span.mark", { "aria-hidden": "true" }, "✓"),
        `Dieses Album wurde am ${fmt.when(last.created_at)} ausgedünnt: ${fmt.num(last.photos)} Bilder liegen in `,
        h("code", {}, "_Aussortiert"), "."),
      h("button.ghost.small", { type: "button", onclick: () => undoFlow(info.album_id, last, () => openAlbum(info.path, err)) },
        "Rückgängig machen"))
    : null;
  const skipOffline = h("input", { type: "checkbox", id: "skip-offline", checked: true });
  const done = info.pending === 0 && info.faces_pending === 0;
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

  showHud("check", {
    title: [
      h("p.eyebrow", {}, "Vorprüfung"),
      h("h1", {}, info.name),
      h("p.path", {}, info.path),
      applied,
      warnings.length
        ? h("ul.warnings", {}, warnings.map((w) => h("li", {}, h("span.mark", { "aria-hidden": "true" }, "!"), w)))
        : null,
      info.offline
        ? h("label.check", { for: "skip-offline" }, skipOffline, h("span.box", { "aria-hidden": "true" }),
          "Cloud-Bilder überspringen (nichts herunterladen)")
        : null,
      err],
    core: [start, h("button.link", { type: "button", onclick: startScreen }, "Anderen Ordner wählen")],
    side: [h("div.readouts", {},
      readout("JPEG-Bilder", fmt.num(info.photos)),
      readout("Gesamtgröße", fmt.bytes(info.total_bytes)),
      readout("Ignoriert", fmt.num(info.ignored), "Andere Formate, Videos usw. – werden nie verschoben"),
      readout("Schon analysiert", fmt.num(info.cached)),
      readout("Geschätzte Dauer", done ? "sofort" : `≈ ${fmt.duration(info.estimate_seconds)}`))],
  }, "ready", { total: info.photos, reset: true });
  start.focus();
}

// ── 1.3 Analyse läuft ─────────────────────────────────────────────────────────────

/** Analysis screen; `cancel` is shown below. Returns the functions that feed it with progress. */
function progressView(eyebrow, name, cancel, note, err) {
  const percent = h("span.percent", {}, "0");
  const counter = h("span.counter", {}, "–");
  const fill = h("span.meter-fill");
  const label = h("span.stage-label", {}, STAGE_LABEL.scan);
  const eta = h("dd.mono", {}, "–");
  const rate = h("dd.mono", {}, "–");
  const elapsed = h("dd.mono", {}, "0 s");
  const current = h("dd.mono.current", {}, " ");
  const meter = h("div.meter", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, h("span.meter-track", {}, fill));
  showHud("work", {
    title: [h("p.eyebrow", {}, eyebrow), h("h1", {}, name), label],
    core: [h("span.percent-row", {}, percent, h("span.percent-sign", {}, "%")), counter],
    side: [h("dl.progress-facts", {},
      h("dt", {}, "Restzeit"), eta, h("dt", {}, "Laufzeit"), elapsed, h("dt", {}, "Tempo"), rate, h("dt", {}, "Aktuelles Bild"), current)],
    foot: [meter, h("div.actions", {}, cancel, note), err],
  }, "analyze", { reset: true });
  const started = performance.now();
  ui.timer = setInterval(() => { elapsed.textContent = fmt.duration((performance.now() - started) / 1000); }, 1000);
  let first = null; // (time, done) when the stage began, for the measured speed
  return {
    update(p) {
      label.textContent = STAGE_LABEL[p.stage] ?? label.textContent;
      const ratio = p.total ? p.done / p.total : 0;
      fill.style.transform = `scaleX(${ratio})`;
      meter.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
      percent.textContent = String(Math.floor(ratio * 100));
      counter.textContent = p.total ? `${fmt.frame(p.done, p.total)} / ${fmt.num(p.total)} Bilder` : "–";
      eta.textContent = p.eta_seconds ? `≈ ${fmt.duration(p.eta_seconds)}` : p.stage === "analyze" || p.stage === "faces" ? "wird berechnet …" : "–";
      current.textContent = p.current_file ?? " ";
      current.title = p.current_file ?? "";
      const now = performance.now();
      if (!first || first.stage !== p.stage || p.done < first.done) first = { stage: p.stage, at: now, done: p.done };
      const dt = (now - first.at) / 1000;
      rate.textContent = dt > 2 && p.done > first.done ? `${new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format((p.done - first.done) / dt)} Bilder/s` : "–";
      stage.set({ mode: p.stage === "scan" ? "scan" : p.stage === "faces" ? "faces" : "analyze", done: p.done, total: p.total });
    },
  };
}

function finishAnimation() {
  stage.set({ mode: "done" });
  return new Promise((r) => setTimeout(r, reducedMotion() ? 0 : DONE_MS));
}

function progressScreen(info) {
  setCrumb(info.name);
  const albumId = info.album_id;
  const err = errorLine();
  const cancel = h("button.ghost", {
    type: "button",
    onclick: async () => {
      cancel.disabled = true;
      cancel.textContent = "Wird angehalten …";
      try { await api.cancel(albumId); } catch (e) { showError(err, e); }
    },
  }, "Abbrechen");
  const view = progressView("Analyse", info.name, cancel,
    h("span.note", {}, "Abbrechen ist jederzeit möglich – beim nächsten Öffnen geht es weiter."), err);
  setStatus("Analyse läuft – Sie können den Laptop weiter benutzen");

  const source = api.events(albumId);
  ui.events = source;
  source.onmessage = async (event) => {
    const data = JSON.parse(event.data);
    const p = data.progress;
    if (p) view.update(p);
    for (const id of data.finished.slice(-4)) stage.addPhoto(api.thumbUrl(albumId, { id, v: 0 }));
    if (data.state === "running") return;
    source.close();
    ui.events = null;
    if (data.state === "done") {
      await finishAnimation();
      reviewScreen(albumId);
    } else if (data.state === "cancelled") cancelledScreen(info, p);
    else showError(err, data.error || "Die Analyse ist fehlgeschlagen.");
  };
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) showError(err, "Verbindung zum Programm verloren.");
  };
}

/** Preview of the animation with painted pictures; reads nothing and changes nothing. */
function demoScreen() {
  setCrumb("Vorschau");
  setStatus("Vorschau mit gemalten Beispielbildern – es wird nichts analysiert");
  const back = h("button.ghost", { type: "button", onclick: startScreen }, "Vorschau beenden");
  const view = progressView("Vorschau", "So sieht die Analyse aus", back,
    h("span.note", {}, "Beispiel mit 480 gemalten Bildern. Das Design lässt sich oben rechts umschalten."), errorLine());
  const total = 480;
  let tick = 0;
  ui.demo = setInterval(() => {
    tick += 1;
    const k = tick % 190; // 8 ticks per second: scan, analysis, faces, done, again
    if (k < 14) view.update({ stage: "scan", done: 0, total: 0 });
    else if (k < 134) {
      const done = Math.round(((k - 14) / 120) * total);
      view.update({ stage: "analyze", done, total, eta_seconds: (134 - k) / 8, current_file: `Beispiel/IMG_${String(done).padStart(4, "0")}.jpg` });
      if (k % 3 === 0) stage.addPhoto(samplePhoto(done));
    } else if (k < 174) {
      view.update({ stage: "faces", done: Math.round(((k - 134) / 40) * 60), total: 60, eta_seconds: (174 - k) / 8, current_file: "Beispiel" });
      if (k % 3 === 0) stage.addPhoto(samplePhoto(k));
    } else if (k === 174) stage.set({ mode: "done" });
    else if (k === 188) stage.set({ mode: "scan", reset: true });
  }, 125);
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

// ── 1.4 Prüf-Übersicht ────────────────────────────────────────────────────────────

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
  show();
  setCrumb(view.name);
  ui.review = mountReview(main, view, { setStatus, setCrumb, setFooter, startScreen,
    reload: () => reviewScreen(albumId) });
}

// ── boot ──────────────────────────────────────────────────────────────────────────

await initDesign(document.getElementById("designs"));
api.version().then((v) => { document.getElementById("version").textContent = `v${v.version}`; }).catch(() => {});
startScreen();
