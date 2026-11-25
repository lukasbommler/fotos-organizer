// Which photos the user really looked at (D20), for the calibration record only: a tile that
// was on screen for DWELL_MS, or a photo in the large view / comparison. The proposal shown at
// that moment is part of the mark: a photo seen as "keep" is not seen as "sort out".
import { api } from "./api.js";

export const SEEN = { group: 1, sheet: 2, large: 4, confirmed: 8 };
export const LARGE_DWELL_MS = 700;
const DWELL_MS = 1000;
const TICK_MS = 250;
const FLUSH_MS = 2000;
const IDLE_MS = 20000; // no mouse or keyboard: probably nobody in front of the screen
const INPUT = ["pointermove", "pointerdown", "wheel", "keydown"];
const MIN_VISIBLE = 0.6; // share of the tile inside the sheet

export class SeenTracker {
  constructor(store, root) {
    this.store = store;
    this.enabled = Boolean(store.stats) && !store.stats.hold;
    this.visible = new Map(); // figure -> {key, since}
    this.sent = new Map(); // "id:auto" -> bits already reported
    this.pending = new Map(); // "id:auto" -> {id, auto, how}
    if (!this.enabled) return;
    this.lastInput = performance.now();
    this.onInput = () => { this.lastInput = performance.now(); };
    for (const type of INPUT) document.addEventListener(type, this.onInput, { capture: true, passive: true });
    this.observer = new IntersectionObserver((entries) => this._onIntersect(entries), { root, threshold: MIN_VISIBLE });
    this.timer = setInterval(() => this._tick(), TICK_MS);
    this.flushTimer = setInterval(() => this.flush(), FLUSH_MS);
  }

  observe(fig) { this.observer?.observe(fig); }

  unobserve(fig) {
    this.observer?.unobserve(fig);
    this.visible.delete(fig);
  }

  reset() {
    this.observer?.disconnect();
    this.visible.clear();
  }

  _key(id) { return `${id}:${this.store.decision(id).auto ? 1 : 0}`; }

  _onIntersect(entries) {
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= MIN_VISIBLE - 0.01) {
        this.visible.set(e.target, { key: this._key(Number(e.target.dataset.id)), since: performance.now() });
      } else {
        this.visible.delete(e.target);
      }
    }
  }

  _tick() {
    const now = performance.now();
    // nobody looks at tiles behind an overlay, in a minimised window or while away
    // ASSUMPTION: document.hasFocus() is not used, it may stay false in an embedded window.
    const covered = document.hidden || now - this.lastInput > IDLE_MS || document.querySelector(".viewer, .dialog-backdrop, .xl");
    for (const [fig, state] of this.visible) {
      const id = Number(fig.dataset.id);
      const key = this._key(id);
      const img = fig.querySelector("img");
      if (covered || key !== state.key || !img?.complete) {
        state.key = key;
        state.since = now;
      } else if (now - state.since >= DWELL_MS) {
        this.mark(id, fig.closest(".bundle-open") ? SEEN.group : SEEN.sheet);
      }
    }
  }

  mark(id, how) {
    if (!this.enabled || this.store.decision(id).unanalysed) return;
    const key = this._key(id);
    const have = this.sent.get(key) ?? 0;
    if ((have & how) === how) return;
    this.sent.set(key, have | how);
    const auto = this.store.decision(id).auto;
    this.sent.delete(`${id}:${auto ? 0 : 1}`); // the server keeps the latest proposal only
    const entry = this.pending.get(key) ?? { id, auto, how: 0 };
    entry.how |= how;
    this.pending.set(key, entry);
  }

  /** Send what is new; a lost mark only means one photo less for the calibration. */
  async flush() {
    if (!this.pending.size) return;
    const items = [...this.pending.values()].map((e) => [e.id, e.how, e.auto]);
    this.pending.clear();
    try { await api.seen(this.store.albumId, items); } catch { /* not worth disturbing the user */ }
  }

  destroy() {
    clearInterval(this.timer);
    clearInterval(this.flushTimer);
    for (const type of INPUT) document.removeEventListener(type, this.onInput, { capture: true });
    this.reset();
    this.flush();
  }
}
