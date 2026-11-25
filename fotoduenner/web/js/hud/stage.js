// Animated background of start, pre-check and analysis (D21). The stage owns canvas, clock and
// state; a scene (orbit, holo, nova) only draws. Everything shown is real: progress, number of
// photos and the previews of photos just analysed.
//
// The analysis needs the CPU, so the stage is frugal: 30 fps at most, limited pixels, less when a
// frame takes long, nothing while hidden, and a still picture with "reduced motion".
import { reducedMotion } from "../util.js";
import { holo } from "./holo.js";
import { nova } from "./nova.js";
import { orbit } from "./orbit.js";
import { clamp, lerp } from "./util.js";

const SCENES = { orbit, holo, nova };
const MAX_PIXELS = 1.5e6;
const FRAME_MS = [33, 50, 80]; // quality levels: full, saving, minimal
const SLOW_DRAW_MS = 14;
const QUEUE_LIMIT = 10;

/** Centre-cropped small copy of a preview, so drawing it every frame stays cheap. */
function thumb(img, w = 132, h = 99) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const iw = img.naturalWidth || img.width;
  const ih = img.naturalHeight || img.height;
  const s = Math.max(w / iw, h / ih);
  c.getContext("2d").drawImage(img, (w - iw * s) / 2, (h - ih * s) / 2, iw * s, ih * s);
  return c;
}

export class HudStage {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.state = { mode: "idle", done: 0, total: 0 };
    this.p = 0; // smoothed progress
    this.energy = 0.3; // smoothed speed of the machine
    this.t = 0;
    this.modeAt = 0;
    this.photos = []; // {img, born, n}
    this.queue = [];
    this.count = 0;
    this.lastPhoto = 0;
    this.level = 0;
    this.drawMs = 0;
    this.running = false;
    this.last = 0;
    this._frame = this._frame.bind(this);
    this._resize = this._resize.bind(this);
    this._visible = () => { if (this.running && !document.hidden) this._kick(); };
    addEventListener("resize", this._resize);
    document.addEventListener("visibilitychange", this._visible);
  }

  setDesign(name) {
    this.scene = (SCENES[name] ?? orbit)();
    this._resize();
    this._still();
  }

  start() {
    this.canvas.hidden = false;
    if (this.running) return;
    this.running = true;
    this._resize();
    this._kick();
  }

  stop() {
    this.running = false;
    this.canvas.hidden = true;
    this.photos = [];
    this.queue = [];
  }

  /** mode: idle · scan (folder being read) · ready · analyze · faces · done */
  set(state) {
    if (state.mode && state.mode !== this.state.mode) this.modeAt = this.t;
    Object.assign(this.state, state);
    if (state.reset) this.p = 0;
    this._still();
  }

  /** A photo just analysed; `source` = URL or something drawable. */
  addPhoto(source) {
    if (reducedMotion() || this.queue.length >= QUEUE_LIMIT) return;
    this.queue.push(source);
  }

  _kick() {
    if (reducedMotion()) { this._still(); return; }
    this.last = performance.now();
    requestAnimationFrame(this._frame);
  }

  _resize() {
    const w = this.canvas.clientWidth || innerWidth;
    const h = this.canvas.clientHeight || innerHeight;
    const scale = Math.min(devicePixelRatio || 1, Math.sqrt(MAX_PIXELS / Math.max(1, w * h)));
    this.canvas.width = Math.round(w * scale);
    this.canvas.height = Math.round(h * scale);
    this.w = w;
    this.h = h;
    this.scale = scale;
    this._still();
  }

  _still() {
    if (reducedMotion() && this.running && this.scene) { this.p = this._target(); this._draw(0); }
  }

  _target() {
    const { done, total, mode } = this.state;
    if (mode === "done") return 1;
    return total ? clamp(done / total) : 0;
  }

  _frame(now) {
    if (!this.running || document.hidden) return;
    requestAnimationFrame(this._frame);
    const dt = now - this.last;
    if (dt < FRAME_MS[this.level] - 4) return;
    this.last = now;
    const start = performance.now();
    this._step(Math.min(0.1, dt / 1000), now);
    this._draw(Math.min(0.1, dt / 1000));
    this.drawMs = lerp(this.drawMs, performance.now() - start, 0.05);
    if (this.drawMs > SLOW_DRAW_MS && this.level < FRAME_MS.length - 1) { this.level += 1; this.drawMs = 0; }
  }

  _step(dt, now) {
    this.t += dt;
    const goal = { idle: 0.3, scan: 1.5, ready: 0.45, analyze: 1, faces: 1.15, done: 2.4 }[this.state.mode] ?? 0.3;
    this.energy = lerp(this.energy, goal, 1 - Math.exp(-dt * 2.5));
    this.p = lerp(this.p, this._target(), 1 - Math.exp(-dt * 3));
    if (this.queue.length && now - this.lastPhoto > this.scene.photoGap && this.photos.length < 8) {
      this.lastPhoto = now;
      this._take(this.queue.shift());
    }
    this.photos = this.photos.filter((f) => this.t - f.born < this.scene.photoLife);
  }

  _take(source) {
    const put = (img) => { this.photos.push({ img: thumb(img), born: this.t, n: this.count++ }); };
    if (typeof source !== "string") { put(source); return; }
    const img = new Image();
    img.decoding = "async";
    img.onload = () => { if (this.running) put(img); };
    img.src = source;
  }

  _draw(dt) {
    const { ctx } = this;
    ctx.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, this.w, this.h);
    const core = document.querySelector(".hud-core")?.getBoundingClientRect();
    const box = this.canvas.getBoundingClientRect();
    this.scene.draw(ctx, {
      w: this.w, h: this.h, t: this.t, dt, p: this.p, energy: this.energy, mode: this.state.mode,
      since: this.t - this.modeAt, total: this.state.total, photos: this.photos, saving: this.level > 0,
      core: core && core.width ? { x: core.left - box.left + core.width / 2, y: core.top - box.top + core.height / 2 } : null,
    });
  }
}
