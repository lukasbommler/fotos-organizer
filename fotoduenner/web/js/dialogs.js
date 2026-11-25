// Dialogs for "Übernehmen" and "Rückgängig" (04 §3): confirmation, progress, result.
import { api } from "./api.js";
import { cropMarks, fill, fmt, h } from "./util.js";

const REASONS = [
  ["duplicate", "Exakte Kopien"],
  ["series", "Fast gleiche Serienbilder"],
  ["motif", "Ähnliche Motive"],
  ["quality", "Technisch schwach"],
  ["manual", "Von Ihnen aussortiert"],
];
const POLL_MS = 150;
const FOCUSABLE = "button:not(:disabled), [href], input:not(:disabled), [tabindex]:not([tabindex='-1'])";

/** Modal dialog in the style of the panels. Esc closes unless a job is running. */
function modal({ eyebrow, title, onClose }) {
  const last = document.activeElement;
  const titleEl = h("h2.dialog-title", { id: "dialog-title" }, title);
  const eyebrowEl = h("p.eyebrow", {}, eyebrow);
  const body = h("div.dialog-body");
  const actions = h("div.actions.dialog-actions");
  const box = h("section.panel.dialog", { role: "dialog", "aria-modal": "true", "aria-labelledby": "dialog-title" },
    ...cropMarks(), eyebrowEl, titleEl, body, actions);
  const backdrop = h("div.dialog-backdrop", {}, box);
  let busy = false;
  let closed = false;

  function close() {
    if (closed) return;
    closed = true;
    backdrop.remove();
    last?.focus?.();
    onClose?.();
  }
  backdrop.addEventListener("keydown", (e) => {
    e.stopPropagation(); // review shortcuts (Space, K, X, Strg + Z) must not act behind the dialog
    if (e.key === "Escape" && !busy) { e.preventDefault(); close(); return; }
    if (e.key !== "Tab") return;
    const items = [...box.querySelectorAll(FOCUSABLE)];
    if (!items.length) { e.preventDefault(); return; }
    const i = items.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); items.at(-1).focus(); }
    else if (!e.shiftKey && i === items.length - 1) { e.preventDefault(); items[0].focus(); }
  });
  document.body.append(backdrop);
  box.tabIndex = -1;
  box.focus();
  return {
    body, actions, titleEl, eyebrowEl, close,
    setBusy(value) { busy = value; },
    focus() { (actions.querySelector("button.primary:not(:disabled)") ?? actions.querySelector("button") ?? box).focus(); },
  };
}

function progressView(label) {
  const bar = h("span.meter-fill");
  const meter = h("div.meter", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" },
    h("span.meter-track", {}, bar));
  const count = h("span.mono", {}, "–");
  const current = h("p.dialog-current.mono", {}, " ");
  return {
    el: h("div", {}, h("p.lead", {}, label), meter, h("p.dialog-count", {}, count), current),
    update(job) {
      const ratio = job.total ? job.done / job.total : 0;
      bar.style.transform = `scaleX(${ratio})`;
      meter.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
      count.textContent = job.total ? `${fmt.num(job.done)} von ${fmt.num(job.total)} Dateien` : "–";
      current.textContent = job.current ?? " ";
    },
  };
}

/** Poll the running job until it ends; returns the final status. */
async function follow(albumId, onUpdate) {
  for (;;) {
    const status = await api.applyStatus(albumId);
    if (status.job) onUpdate(status.job);
    if (status.job && status.job.state !== "running") return status;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

function failureList(items, count, title) {
  if (!count) return null;
  return h("div.dialog-failures", {},
    h("p.dialog-sub", {}, title),
    h("ul.failures", {}, items.map((f) => h("li", {}, h("span.failure-name.mono", {}, f.name), h("span.failure-why", {}, f.error)))),
    count > items.length ? h("p.note", {}, `… und ${fmt.num(count - items.length)} weitere`) : null);
}

const plural = (n, one, many) => `${fmt.num(n)} ${n === 1 ? one : many}`;

/**
 * Shared flow: confirmation → progress → result. `start()` posts the job, `result(status, dlg)`
 * fills the result page. Closing after any job reloads the screen (`onFinished`).
 */
function jobDialog({ eyebrow, title, progressLabel, onFinished }) {
  let ran = false;
  const dlg = modal({ eyebrow, title, onClose: () => { if (ran) onFinished(); } });

  async function run(start, result) {
    ran = true;
    dlg.setBusy(true);
    const prog = progressView(progressLabel);
    fill(dlg.body, prog.el);
    fill(dlg.actions);
    let status;
    try {
      await start();
      status = await follow(dlg.albumId, prog.update);
    } catch (e) {
      dlg.setBusy(false);
      fill(dlg.body, h("p.error", { role: "alert" }, e.message));
      fill(dlg.actions, h("button.primary", { type: "button", onclick: dlg.close }, "Schließen"));
      dlg.focus();
      return;
    }
    dlg.setBusy(false);
    if (status.job.state === "failed") {
      fill(dlg.body, h("p.error", { role: "alert" }, status.job.error || "Der Vorgang ist fehlgeschlagen."),
        h("p.note", {}, "Alles bis dahin Verschobene ist protokolliert und lässt sich rückgängig machen."));
      fill(dlg.actions, h("button.primary", { type: "button", onclick: dlg.close }, "Schließen"));
    } else {
      result(status);
    }
    dlg.focus();
  }
  return Object.assign(dlg, { run });
}

function explorerButton(albumId, dlg) {
  const note = h("p.note.dialog-flash", { hidden: true });
  const button = h("button.ghost", { type: "button", onclick: async () => {
    try { await api.openFolder(albumId); } catch (e) { note.hidden = false; note.textContent = e.message; }
  } }, "Ordner im Explorer öffnen");
  dlg.body.append(note);
  return button;
}

/** "Übernehmen …": summary by reason, then move (04 §3). */
export async function applyFlow(albumId, onFinished) {
  const dlg = jobDialog({ eyebrow: "Übernehmen", title: "Aussortierte Bilder verschieben",
    progressLabel: "Bilder werden nach „_Aussortiert“ verschoben …", onFinished });
  dlg.albumId = albumId;
  dlg.body.append(h("p.lead", {}, "Zusammenfassung wird berechnet …"));
  let pre;
  try {
    pre = await api.applyPreview(albumId);
  } catch (e) {
    fill(dlg.body, h("p.error", { role: "alert" }, e.message));
    fill(dlg.actions, h("button.primary", { type: "button", onclick: dlg.close }, "Schließen"));
    dlg.focus();
    return;
  }
  const problems = [...pre.blocking, ...pre.warnings];
  fill(dlg.body,
    h("p.lead", {}, `${plural(pre.photos, "Bild", "Bilder")} (${fmt.bytes(pre.bytes)}) werden in den Unterordner `,
      h("code", {}, "_Aussortiert"), " verschoben. Nichts wird gelöscht. Sie können dies jederzeit rückgängig machen."),
    h("dl.reason-list", {}, REASONS.filter(([key]) => pre.by_reason[key]).flatMap(([key, label]) =>
      [h("dt", {}, label), h("dd.mono", {}, fmt.num(pre.by_reason[key]))])),
    pre.files > pre.photos
      ? h("p.note", {}, `Dazu ${plural(pre.files - pre.photos, "Begleitdatei", "Begleitdateien")} (.xmp), die mit ihrem Bild mitwandern.`)
      : null,
    problems.length ? h("ul.warnings", {}, problems.map((w) => h("li", {}, h("span.mark", { "aria-hidden": "true" }, "!"), w))) : null,
    h("p.path", { title: "Zielordner" }, pre.target),
    pre.recorded
      ? h("p.note", {}, "Zur Feinabstimmung merkt sich FotoDünner je Bild den Vorschlag, Ihre Entscheidung und ob Sie es angesehen haben – nur auf diesem PC.")
      : null);
  const go = h("button.primary", { type: "button", disabled: !pre.photos || pre.blocking.length > 0,
    onclick: () => dlg.run(() => api.apply(albumId, pre.photo_ids), applied) }, "Verschieben");
  fill(dlg.actions, h("button.ghost", { type: "button", onclick: dlg.close }, "Abbrechen"), go);
  dlg.focus();

  function applied(status) {
    const report = status.job.report;
    // nothing moved at all: the batch is closed, "Übernehmen …" can simply be used again
    const last = status.apply.last?.batch_id === status.job.batch_id ? status.apply.last : null;
    dlg.titleEl.textContent = report.photos || last ? "Verschoben" : "Nichts verschoben";
    fill(dlg.body,
      h("p.lead.dialog-result", {}, `${fmt.num(report.photos)} verschoben, ${fmt.num(report.failed_count)} Fehler.`),
      last
        ? h("p.note", {}, "Wenn Sie zufrieden sind, können Sie den Ordner ", h("code", {}, "_Aussortiert"),
          " später selbst löschen. Bis dahin lässt sich alles mit „Rückgängig“ zurückholen.")
        : h("p.note", {}, "Alle Bilder liegen unverändert im Album. Bitte die Ursache beheben und noch einmal „Übernehmen …“ wählen."),
      failureList(last?.pending ?? report.failed, last?.pending_count ?? report.failed_count, "Nicht verschoben:"));
    const retry = last?.pending_count
      ? h("button.ghost", { type: "button", onclick: () => dlg.run(() => api.applyRetry(albumId, last.batch_id), applied) }, "Erneut versuchen")
      : null;
    fill(dlg.actions, explorerButton(albumId, dlg), retry, h("button.primary", { type: "button", onclick: dlg.close }, "Fertig"));
  }
}

/** "Erneut versuchen" from the review banner: failed or interrupted files of the last batch. */
export function retryFlow(albumId, last, onFinished) {
  const dlg = jobDialog({ eyebrow: "Übernehmen", title: "Restliche Dateien verschieben",
    progressLabel: "Dateien werden nach „_Aussortiert“ verschoben …", onFinished });
  dlg.albumId = albumId;
  fill(dlg.body,
    h("p.lead", {}, `${plural(last.pending_count, "Datei wurde", "Dateien wurden")} beim letzten Übernehmen nicht verschoben.`),
    failureList(last.pending, last.pending_count, "Zuletzt gemeldet:"));
  const go = h("button.primary", { type: "button", onclick: () => dlg.run(() => api.applyRetry(albumId, last.batch_id), done) }, "Erneut versuchen");
  fill(dlg.actions, h("button.ghost", { type: "button", onclick: dlg.close }, "Abbrechen"), go);
  dlg.focus();

  function done(status) {
    const report = status.job.report;
    const now = status.apply.last;
    dlg.titleEl.textContent = "Verschoben";
    fill(dlg.body,
      h("p.lead.dialog-result", {}, `${fmt.num(report.files)} ${report.files === 1 ? "Datei" : "Dateien"} verschoben, ${fmt.num(report.failed_count)} Fehler.`),
      failureList(now?.pending ?? [], now?.pending_count ?? 0, "Weiterhin nicht verschoben:"));
    fill(dlg.actions, h("button.primary", { type: "button", onclick: dlg.close }, "Fertig"));
  }
}

/** "Rückgängig": bring the last batch back (04 §3) – also without analysis (pre-check screen). */
export function undoFlow(albumId, last, onFinished) {
  const dlg = jobDialog({ eyebrow: "Rückgängig", title: "Übernehmen rückgängig machen",
    progressLabel: "Bilder werden zurückgeholt …", onFinished });
  dlg.albumId = albumId;
  fill(dlg.body,
    h("p.lead", {}, `Übernahme vom ${fmt.when(last.created_at)}: ${plural(last.photos, "Bild kommt", "Bilder kommen")} aus `,
      h("code", {}, "_Aussortiert"), " zurück an den alten Platz."),
    last.files > last.photos
      ? h("p.note", {}, `Dazu ${plural(last.files - last.photos, "Begleitdatei", "Begleitdateien")} (.xmp).`)
      : null,
    h("p.note", {}, "Ihre Entscheidungen bleiben erhalten – danach sehen Sie wieder die Übersicht mit allen Bildern."));
  const go = h("button.primary", { type: "button", onclick: () => dlg.run(() => api.undo(albumId, last.batch_id), done) }, "Zurückholen");
  fill(dlg.actions, h("button.ghost", { type: "button", onclick: dlg.close }, "Abbrechen"), go);
  dlg.focus();

  function done(status) {
    const report = status.job.report;
    dlg.titleEl.textContent = "Zurückgeholt";
    fill(dlg.body,
      h("p.lead.dialog-result", {}, `${fmt.num(report.photos)} zurückgeholt, ${fmt.num(report.failed_count)} Fehler.`),
      report.notes.length ? h("ul.warnings", {}, report.notes.map((n) => h("li", {}, h("span.mark", { "aria-hidden": "true" }, "!"), n))) : null,
      failureList(report.failed, report.failed_count, "Nicht zurückgeholt:"),
      status.apply.last ? h("p.note", {}, `Eine frühere Übernahme vom ${fmt.when(status.apply.last.created_at)} lässt sich ebenfalls rückgängig machen.`) : null);
    fill(dlg.actions, h("button.primary", { type: "button", onclick: dlg.close }, "Fertig"));
  }
}
