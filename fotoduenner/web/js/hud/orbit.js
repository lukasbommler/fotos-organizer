// Design 1 "Reaktor": concentric instrument rings around a glowing core. Photos fly in, are
// scanned in orbit and drawn into the ring, whose ticks are the real progress.
import { clamp, easeInOut, easeOut, glowSprite, lerp } from "./util.js";

const TAU = Math.PI * 2;
const ICE = "70,217,255";
const WHITE = "224,247,255";
const WARM = "255,179,71";
// docking positions above and below the core, away from the text columns
const DOCKS = [-126, 54, -54, 126].map((d) => (d * Math.PI) / 180);

const RINGS = [
  { r: 1.1, w: 1.5, speed: 0.1, arcs: [[0, 0.9], [1.1, 0.35], [1.7, 1.6], [3.6, 0.5], [4.4, 1.5]], a: 0.5 },
  { r: 0.97, w: 7, speed: -0.16, arcs: [[0.2, 0.5], [2.0, 0.9], [4.1, 0.3], [4.7, 0.8]], a: 0.22 },
  { r: 0.86, w: 1.5, speed: 0.26, arcs: [[0, 2.2], [2.6, 0.7], [3.9, 1.8]], a: 0.6 },
  { r: 0.77, w: 3.5, speed: -0.42, arcs: [[0.4, 0.25], [0.8, 0.25], [1.2, 0.25], [3.5, 0.25], [3.9, 0.25], [4.3, 0.25]], a: 0.75 },
];

export function orbit() {
  const glow = glowSprite(ICE);
  const hot = glowSprite(WHITE, 64);
  const dust = Array.from({ length: 80 }, () => ({ a: Math.random() * TAU, d: Math.random(), s: 0.5 + Math.random() }));

  function arc(ctx, x, y, r, from, len) {
    ctx.beginPath();
    ctx.arc(x, y, r, from, from + len);
    ctx.stroke();
  }

  function photo(ctx, f, S, x, y, R) {
    const k = (S.t - f.born) / scene.photoLife;
    const angle = DOCKS[f.n % DOCKS.length] + Math.sin(f.n * 1.7) * 0.06;
    const w = R * 0.5;
    const ringAngle = -Math.PI / 2 + S.p * TAU;
    let px; let py; let scale = 1; let alpha = 1;
    if (k < 0.24) {
      const e = easeOut(k / 0.24);
      const d = lerp(2.5, 1.48, e) * R;
      px = x + Math.cos(angle + (1 - e) * 0.9) * d;
      py = y + Math.sin(angle + (1 - e) * 0.9) * d;
      alpha = e;
      scale = lerp(0.4, 1, e);
    } else if (k < 0.76) {
      const drift = (k - 0.24) * 0.12;
      px = x + Math.cos(angle + drift) * R * 1.48;
      py = y + Math.sin(angle + drift) * R * 1.48;
    } else {
      const e = easeInOut((k - 0.76) / 0.24);
      const sx = x + Math.cos(angle + 0.0624) * R * 1.48;
      const sy = y + Math.sin(angle + 0.0624) * R * 1.48;
      px = lerp(sx, x + Math.cos(ringAngle) * R * 1.0, e);
      py = lerp(sy, y + Math.sin(ringAngle) * R * 1.0, e);
      scale = lerp(1, 0.04, e);
      alpha = 1 - e * 0.5;
    }
    const hw = (w / 2) * scale;
    const hh = hw * 0.75;
    ctx.globalAlpha = alpha * 0.95;
    ctx.drawImage(f.img, px - hw, py - hh, hw * 2, hh * 2);
    if (scale < 0.5) { ctx.globalCompositeOperation = "lighter"; ctx.drawImage(hot, px - 16, py - 16, 32, 32); ctx.globalCompositeOperation = "source-over"; return; }
    // target brackets, scan line, beam to the core
    const c = 9 * scale;
    const o = 5;
    ctx.strokeStyle = `rgba(${ICE},0.95)`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const ex = px + sx * (hw + o);
      const ey = py + sy * (hh + o);
      ctx.moveTo(ex - sx * c, ey);
      ctx.lineTo(ex, ey);
      ctx.lineTo(ex, ey - sy * c);
    }
    ctx.stroke();
    if (k >= 0.24 && k < 0.76) {
      const s = (k - 0.24) / 0.52;
      const ly = py - hh + (0.5 - Math.abs(((s * 2) % 1) - 0.5)) * 2 * hh * 2;
      const warm = S.mode === "faces";
      ctx.globalAlpha = 0.22;
      ctx.fillStyle = `rgb(${warm ? WARM : ICE})`;
      ctx.fillRect(px - hw, ly - 10, hw * 2, 10);
      ctx.globalAlpha = 0.95;
      ctx.fillRect(px - hw - 3, ly - 1, hw * 2 + 6, 1.5);
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1;
      ctx.beginPath();
      const ux = (x - px) / Math.hypot(x - px, y - py);
      const uy = (y - py) / Math.hypot(x - px, y - py);
      ctx.moveTo(px + ux * (hh + 12), py + uy * (hh + 12));
      ctx.lineTo(x - ux * R * 1.2, y - uy * R * 1.2);
      ctx.stroke();
    }
  }

  const scene = {
    photoLife: 3.4,
    photoGap: 850, // four docks: one photo each
    draw(ctx, S) {
      const x = S.core?.x ?? S.w / 2;
      const y = S.core?.y ?? S.h / 2;
      const R = S.w < 900 ? Math.min(S.w * 0.31, 130) : Math.min(S.h * 0.235, S.w * 0.14);
      const burst = S.mode === "done" ? clamp(S.since / 1.2) : 0;
      const t = S.t;

      // cross-hair over the whole stage and faint range circles
      ctx.lineWidth = 1;
      ctx.strokeStyle = `rgba(${ICE},0.07)`;
      ctx.beginPath();
      ctx.moveTo(0, y + 0.5);
      ctx.lineTo(S.w, y + 0.5);
      ctx.moveTo(x + 0.5, 0);
      ctx.lineTo(x + 0.5, S.h);
      ctx.stroke();
      for (const k of [1.75, 2.3, 3.0]) arc(ctx, x, y, R * k, t * 0.02 * k, TAU * 0.9);

      // dust drawn into the core
      ctx.fillStyle = `rgba(${ICE},1)`;
      for (const q of dust) {
        q.d -= S.dt * 0.07 * q.s * (0.4 + S.energy);
        q.a += S.dt * 0.12 * S.energy;
        if (q.d < 0.1) { q.d = 1; q.a = Math.random() * TAU; }
        const d = R * (0.7 + q.d * 2.6);
        ctx.globalAlpha = 0.5 * (1 - q.d);
        ctx.fillRect(x + Math.cos(q.a) * d, y + Math.sin(q.a) * d, 1.6, 1.6);
      }

      ctx.globalCompositeOperation = "lighter";
      const pulse = 0.34 + 0.07 * Math.sin(t * 2.2) + 0.1 * S.energy;
      ctx.globalAlpha = clamp(pulse * (1 - burst * 0.5));
      ctx.drawImage(glow, x - R * 2, y - R * 2, R * 4, R * 4);
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1 - burst * 0.8;

      // radar sweep while the folder is read
      if (S.mode === "scan" && ctx.createConicGradient) {
        const grad = ctx.createConicGradient(t * 3.4, x, y);
        grad.addColorStop(0, `rgba(${ICE},0)`);
        grad.addColorStop(0.8, `rgba(${ICE},0)`);
        grad.addColorStop(1, `rgba(${ICE},0.5)`);
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(x, y, R * 1.7, 0, TAU);
        ctx.arc(x, y, R * 0.66, 0, TAU, true);
        ctx.fill();
      }

      const G = 1 + easeOut(burst) * 0.9;
      // tick ring = progress
      const n = 144;
      const lit = S.p * n;
      for (const on of [false, true]) {
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const wave = S.mode !== "analyze" && S.mode !== "faces" && S.mode !== "done"
            && ((i / n - t * 0.12 * (0.5 + S.energy)) % 1 + 1) % 1 > 0.93;
          if ((i < lit || wave) !== on) continue;
          const a = -Math.PI / 2 + (i / n) * TAU;
          const r0 = R * 1.2 * G;
          const r1 = r0 + (on ? 13 : i % 12 === 0 ? 11 : 6);
          ctx.moveTo(x + Math.cos(a) * r0, y + Math.sin(a) * r0);
          ctx.lineTo(x + Math.cos(a) * r1, y + Math.sin(a) * r1);
        }
        ctx.strokeStyle = on ? `rgba(${ICE},0.95)` : `rgba(${ICE},0.24)`;
        ctx.lineWidth = on ? 2.2 : 1;
        ctx.stroke();
      }

      ctx.lineCap = "butt";
      for (const ring of RINGS) {
        ctx.strokeStyle = `rgba(${ICE},${ring.a})`;
        ctx.lineWidth = ring.w;
        for (const [from, len] of ring.arcs) arc(ctx, x, y, R * ring.r * G, from + t * ring.speed * (0.4 + S.energy * 1.4), len);
      }
      if (S.mode === "faces") { // second pass: an additional warm ring
        ctx.strokeStyle = `rgba(${WARM},0.9)`;
        ctx.lineWidth = 2.5;
        for (let i = 0; i < 3; i++) arc(ctx, x, y, R * 0.92 * G, -t * 1.3 + (i * TAU) / 3, 0.8);
      }

      // progress arc with a bright head
      if (S.p > 0.002) {
        const a = -Math.PI / 2 + S.p * TAU;
        ctx.strokeStyle = `rgba(${WHITE},0.95)`;
        ctx.lineWidth = 4;
        arc(ctx, x, y, R * 1.04 * G, -Math.PI / 2, S.p * TAU);
        ctx.globalCompositeOperation = "lighter";
        ctx.drawImage(hot, x + Math.cos(a) * R * 1.04 * G - 20, y + Math.sin(a) * R * 1.04 * G - 20, 40, 40);
        ctx.globalCompositeOperation = "source-over";
      }

      // reactor blades around the core
      const blades = 30;
      for (let i = 0; i < blades; i++) {
        const a = (i / blades) * TAU - t * 0.18 * S.energy;
        const k = 0.35 + 0.65 * Math.max(0, Math.sin(i * 0.42 - t * 3 * (0.4 + S.energy)));
        ctx.strokeStyle = `rgba(${ICE},${(0.12 + 0.55 * k).toFixed(3)})`;
        ctx.lineWidth = 5;
        arc(ctx, x, y, R * 0.655, a, (TAU / blades) * 0.62);
      }
      ctx.strokeStyle = `rgba(${ICE},0.5)`;
      ctx.lineWidth = 1;
      arc(ctx, x, y, R * 0.6, 0, TAU);
      if (burst > 0) {
        ctx.globalAlpha = 1 - burst;
        ctx.strokeStyle = `rgba(${WHITE},1)`;
        ctx.lineWidth = 3;
        arc(ctx, x, y, R * (1.2 + easeOut(burst) * 3.4), 0, TAU);
      }

      for (const f of S.photos) photo(ctx, f, S, x, y, R);
      ctx.globalAlpha = 1;
    },
  };
  return scene;
}
