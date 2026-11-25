// Review state in the browser: decisions, groups, settings. The server stays the source of
// truth; every change is applied locally first (instant feedback) and then replaced by the
// server's diff. Requests run one after another so their answers arrive in order.
import { api } from "./api.js";

const UNDO_LIMIT = 200;

/** Short stable hash (cyrb53) of a string, base 36. */
function hash(text) {
  let a = 0xdeadbeef;
  let b = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    a = Math.imul(a ^ c, 2654435761);
    b = Math.imul(b ^ c, 1597334677);
  }
  a = Math.imul(a ^ (a >>> 16), 2246822507) ^ Math.imul(b ^ (b >>> 13), 3266489909);
  b = Math.imul(b ^ (b >>> 16), 2246822507) ^ Math.imul(a ^ (a >>> 13), 3266489909);
  return (4294967296 * (2097151 & b) + (a >>> 0)).toString(36);
}

export class ReviewStore {
  constructor(albumId, view) {
    this.albumId = albumId;
    this.photos = view.photos; // capture order, every photo of the album
    this.byId = new Map(view.photos.map((p) => [p.id, p]));
    this.decisions = new Map();
    this.listeners = new Set();
    this.undoStack = [];
    this.queue = Promise.resolve();
    this.error = null;
    const review = view.review ?? { decisions: [], stats: null, groups: [], singles: [], duplicates: [], facts: {} };
    for (const d of review.decisions) this.decisions.set(d.id, d);
    this.stats = review.stats;
    this.done = new Set(review.done ?? []);
    this.setStructure(review);
  }

  setStructure(review) {
    this.groups = review.groups ?? [];
    this.singles = review.singles ?? [];
    this.facts = new Map(Object.entries(review.facts ?? {}).map(([k, v]) => [Number(k), v]));
    this.groupOf = new Map();
    for (const g of this.groups) for (const id of g.ids) this.groupOf.set(id, g);
    // exact copies: one special bundle per original (04 §1.4 "Exakte Kopien")
    const copies = new Map();
    for (const { id, of } of review.duplicates ?? []) {
      if (!copies.has(of)) copies.set(of, [of]);
      copies.get(of).push(id);
    }
    this.copyBundles = [...copies.values()].map((ids, i) => {
      const times = ids.map((id) => this.byId.get(id)?.taken_at ?? 0);
      return { n: `K${i + 1}`, kind: "copies", ids, parts: [ids], ranked: ids, people: false, start: Math.min(...times), end: Math.max(...times) };
    });
    this.copyOf = new Map();
    for (const b of this.copyBundles) for (const id of b.ids.slice(1)) this.copyOf.set(id, b);
  }

  /** Identity of a stack: members and proposals. Another proposal needs a new confirmation. */
  groupSig(g) {
    const ids = [...g.ids].sort((a, b) => a - b);
    return `${ids.length}-${hash(ids.map((id) => `${id}${this.decision(id).auto ? "k" : "r"}`).join(","))}`;
  }

  isDone(g) { return this.done.has(this.groupSig(g)); }

  /** Confirm a stack (or open it again). Confirming also confirms its "Bitte prüfen" photos. */
  async setDone(g, done) {
    const sig = this.groupSig(g);
    if (done) {
      this.done.add(sig);
      const open = g.ids.filter((id) => this.decision(id).review);
      if (open.length) this.setKeep(open, true);
      for (const id of g.ids) this.seen?.mark(id, 8);
    } else {
      this.done.delete(sig);
    }
    this.emit({ type: "done", group: g });
    try {
      await api.groupDone(this.albumId, sig, done);
    } catch (error) {
      if (done) this.done.delete(sig); else this.done.add(sig);
      this.emit({ type: "error", error });
      this.emit({ type: "done", group: g });
    }
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  emit(event) {
    for (const fn of this.listeners) fn(event);
  }

  /** Decision of a photo; photos without analysis are always kept. */
  decision(id) {
    return this.decisions.get(id) ?? { id, keep: true, auto: true, manual: null, review: false, reason: null, code: null, ref: null, unanalysed: true };
  }

  fact(id) {
    return this.facts.get(id) ?? null;
  }

  rejectedBytes() {
    let n = 0;
    let bytes = 0;
    for (const d of this.decisions.values()) {
      if (d.keep) continue;
      n += 1;
      bytes += this.byId.get(d.id)?.size ?? 0;
    }
    return { n, bytes };
  }

  _send(fn) {
    const run = this.queue.then(fn).then((diff) => {
      this.error = null;
      this.apply(diff);
      return diff;
    }).catch((err) => {
      this.error = err;
      this.emit({ type: "error", error: err });
    });
    this.queue = run;
    return run;
  }

  apply(diff) {
    const ids = [];
    for (const d of diff.changed ?? []) {
      this.decisions.set(d.id, d);
      ids.push(d.id);
    }
    if (diff.stats) this.stats = diff.stats;
    const structure = Boolean(diff.groups);
    if (structure) this.setStructure(diff);
    this.emit({ type: "decisions", ids, structure });
  }

  /** Manual value that yields `keep` for this photo; null when it equals the proposal.
   *  Keeping a "Bitte prüfen" photo is a decision of its own: it confirms the photo. */
  manualFor(id, keep) {
    const d = this.decision(id);
    if (keep === null) return null;
    if (keep && d.auto_review) return "keep";
    return keep === d.auto ? null : keep ? "keep" : "reject";
  }

  /** Set photos to keep (true), sort out (false) or back to the proposal (null). */
  setKeep(ids, keep, { undo = true } = {}) {
    const targets = ids.filter((id) => this.decisions.has(id));
    if (!targets.length) return Promise.resolve();
    const before = targets.map((id) => ({ id, manual: this.decision(id).manual }));
    const byManual = new Map();
    for (const id of targets) {
      const manual = this.manualFor(id, keep);
      if (!byManual.has(manual)) byManual.set(manual, []);
      byManual.get(manual).push(id);
    }
    if ([...byManual].every(([manual, list]) => list.every((id) => this.decision(id).manual === manual))) {
      return Promise.resolve(); // nothing changes
    }
    if (undo) {
      this.undoStack.push(before);
      if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
    }
    this._optimistic(byManual);
    let last = Promise.resolve();
    for (const [manual, list] of byManual) last = this._send(() => api.decision(this.albumId, list, manual));
    return last;
  }

  toggle(id) {
    const d = this.decision(id);
    if (d.unanalysed) return Promise.resolve();
    return this.setKeep([id], !d.keep);
  }

  undo() {
    const entry = this.undoStack.pop();
    if (!entry) return false;
    const byManual = new Map();
    for (const { id, manual } of entry) {
      if (!byManual.has(manual)) byManual.set(manual, []);
      byManual.get(manual).push(id);
    }
    this._optimistic(byManual);
    for (const [manual, list] of byManual) this._send(() => api.decision(this.albumId, list, manual));
    return true;
  }

  _optimistic(byManual) {
    const ids = [];
    for (const [manual, list] of byManual) {
      for (const id of list) {
        const d = { ...this.decision(id), manual };
        d.keep = manual === null ? d.auto : manual === "keep";
        d.review = manual === null && Boolean(d.auto_review);
        if (manual === "reject") d.reason = "Von Ihnen aussortiert.";
        if (manual === "keep") d.reason = null;
        this.decisions.set(id, d);
        ids.push(id);
      }
    }
    this.emit({ type: "decisions", ids, structure: false, local: true });
  }

  settings(changes) {
    return this._send(() => api.settings(this.albumId, changes));
  }
}
