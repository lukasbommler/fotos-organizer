// Design 3 "Sternkarte": every photo of the album is a star of a turning galaxy that lights up
// from the centre outwards. Photos rise from their star, are measured and sink back as light.
import { clamp, easeInOut, easeOut, glowSprite, lerp } from "./stage.js";

const TAU = Math.PI * 2;
const GOLDEN = 2.399963;
const ICE = "157,184,255";
const WHITE = "238,242,255";
const WARM = "255,206,140";
const TONES = [WHITE, ICE, WARM]; // real star colours

export function nova() {
  const glow = glowSprite(ICE);
  const star = glowSprite(WHITE, 32);
  const back = Array.from({ length: 150 }, () => ({ x: Math.random(), y: Math.random(), s: Math.random() }));
  const pings = [];
  const pinged = new Set();

  const scene = {
    photoLife: 3.2,
    photoGap: 640,
    draw(ctx, S) {
      const cx = S.w * 0.585;
      const cy = S.h * 0.47;
      const R = Math.min(S.w * 0.34, S.h * 0.62);
      const t = S.t;
      const n = Math.round(clamp(S.total || 620, 260, 1500));
      const burst = S.mode === "done" ? clamp(S.since / 1.3) : 0;
      const gather = S.mode === "scan" ? easeOut(S.since / 1.6) : 1;
      const turn = t * (0.05 + S.energy * 0.05);
      const working = S.mode === "analyze" || S.mode === "faces" || S.mode === "done";
      const lit = S.p * n;
      const TILT = 0.42;

      const pos = (i) => {
        const r = Math.sqrt((i + 0.5) / n);
        const a = i * GOLDEN + turn + r * 1.1;
        const spread = 1 + (1 - gather) * (1.6 + Math.sin(i * 12.9) * 1.4) + easeOut(burst) * (0.5 + (i % 7) * 0.16);
        const x = cx + Math.cos(a) * r * R * spread;
        const bulge = Math.exp(-r * 5) * R * 0.14;
        const y = cy + Math.sin(a) * r * R * TILT * spread + Math.sin(i * 78.2) * (3 + bulge);
        return [x, y, r];
      };

      // distant stars
      ctx.fillStyle = `rgb(${WHITE})`;
      for (const b of back) {
        ctx.globalAlpha = 0.1 + 0.25 * b.s * (0.6 + 0.4 * Math.sin(t * (0.5 + b.s) + b.x * 40));
        ctx.fillRect(((b.x * S.w + t * 2 * b.s) % S.w), b.y * S.h, 1 + b.s, 1 + b.s);
      }

      // core light
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = clamp(0.42 + 0.45 * S.p + 0.06 * Math.sin(t * 1.7)) * (1 - burst * 0.4);
      ctx.drawImage(glow, cx - R * 0.9, cy - R * 0.48, R * 1.8, R * 0.96);
      ctx.globalAlpha = 0.55;
      ctx.drawImage(star, cx - 34, cy - 22, 68, 44);
      ctx.globalCompositeOperation = "source-over";

      // graduated range ellipses; the outer one carries the progress
      const ellipse = (k, from = 0, to = TAU) => { ctx.beginPath(); ctx.ellipse(cx, cy, R * k, R * k * TILT, 0, from, to); ctx.stroke(); };
      ctx.lineWidth = 1;
      ctx.strokeStyle = `rgb(${ICE})`;
      ctx.globalAlpha = 0.12;
      for (const k of [0.36, 0.7, 1.28]) ellipse(k);
      ctx.globalAlpha = 0.3;
      ellipse(1.08);
      for (const on of [false, true]) {
        ctx.beginPath();
        for (let i = 0; i < 120; i++) {
          const k = i / 120;
          if ((working && k < S.p) !== on) continue;
          const a = -Math.PI / 2 + k * TAU;
          const r1 = 1.08 + (on ? 0.05 : i % 10 === 0 ? 0.04 : 0.02);
          ctx.moveTo(cx + Math.cos(a) * R * 1.08, cy + Math.sin(a) * R * 1.08 * TILT);
          ctx.lineTo(cx + Math.cos(a) * R * r1, cy + Math.sin(a) * R * r1 * TILT);
        }
        ctx.globalAlpha = on ? 0.95 : 0.35;
        ctx.strokeStyle = `rgb(${on ? WHITE : ICE})`;
        ctx.lineWidth = on ? 2 : 1;
        ctx.stroke();
      }
      if (S.mode === "scan" || S.mode === "ready" || S.mode === "idle") { // turning bearing line
        const a = t * (S.mode === "scan" ? 2.6 : 0.5);
        ctx.globalAlpha = S.mode === "scan" ? 0.8 : 0.25;
        ctx.strokeStyle = `rgb(${ICE})`;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(a) * R * 1.2, cy + Math.sin(a) * R * 1.2 * TILT);
        ctx.stroke();
      }

      // stars in four passes: waiting, and three colours of lit ones
      ctx.fillStyle = `rgb(${ICE})`;
      for (let i = Math.floor(working ? lit : 0); i < n; i++) {
        const [x, y, r] = pos(i);
        const wave = working ? 0 : Math.max(0, Math.sin(r * 9 - t * 1.6));
        ctx.globalAlpha = (working ? 0.3 : 0.4 + 0.6 * wave) * gather * (1 - burst);
        ctx.fillRect(x, y, 1.8, 1.8);
      }
      if (working) {
        for (let c = 0; c < TONES.length; c++) {
          ctx.fillStyle = `rgb(${TONES[c]})`;
          for (let i = c; i < lit; i += TONES.length) {
            const [x, y] = pos(i);
            const age = clamp((lit - i) / Math.max(12, n * 0.05));
            ctx.globalAlpha = (1 - 0.2 * age) * (1 - burst * 0.9);
            const s = lerp(3.6, 2.3, age);
            ctx.fillRect(x - s / 2, y - s / 2, s, s);
          }
        }
        ctx.globalCompositeOperation = "lighter"; // the newest stars shine
        for (let i = Math.max(0, Math.floor(lit) - 18); i < lit; i++) {
          const [x, y] = pos(i);
          ctx.globalAlpha = 0.75 * (1 - burst);
          ctx.drawImage(star, x - 11, y - 11, 22, 22);
        }
        ctx.globalCompositeOperation = "source-over";
        // lattice of constellations along the frontier
        const from = Math.max(13, Math.floor(lit) - (S.saving ? 24 : 60));
        ctx.strokeStyle = `rgb(${ICE})`;
        ctx.lineWidth = 1;
        for (let i = from; i < lit; i++) {
          const [x, y] = pos(i);
          ctx.globalAlpha = 0.5 * ((i - from) / Math.max(1, lit - from)) * (1 - burst);
          ctx.beginPath();
          for (const k of [8, 13]) {
            const [qx, qy] = pos(i - k);
            if (Math.hypot(qx - x, qy - y) > R * 0.3) continue;
            ctx.moveTo(x, y);
            ctx.lineTo(qx, qy);
          }
          ctx.stroke();
        }
        if (S.mode === "faces") { // second pass: warm waves through the galaxy
          ctx.strokeStyle = `rgb(${WARM})`;
          for (let k = 0; k < 3; k++) {
            const q = (t * 0.35 + k / 3) % 1;
            ctx.globalAlpha = 0.7 * (1 - q);
            ctx.lineWidth = 1.5;
            ellipse(0.08 + q);
          }
        }
      }

      // photos rising from their star
      for (const f of S.photos) {
        const k = (t - f.born) / scene.photoLife;
        const i = Math.max(0, Math.min(n - 1, Math.floor(Math.max(lit, 1) - 1 - (f.n % 5) * 7)));
        f.star ??= i;
        const [sx, sy] = pos(Math.min(n - 1, f.star));
        const lane = (f.n % 5) - 2;
        const lw = Math.min(132, S.w * 0.094);
        const tx = S.w * 0.535 + lane * lw; // between the two text columns
        const ty = S.h * 0.165 + (Math.abs(lane) % 2) * lw * 0.3;
        const up = easeOut(k / 0.28);
        const down = easeInOut((k - 0.74) / 0.26);
        const e = k < 0.74 ? up : 1 - down;
        const x = lerp(sx, tx, e);
        const y = lerp(sy, ty, e);
        const w = lerp(6, lw * 0.88, e);
        const h = w * 0.75;
        ctx.globalAlpha = 0.5 * e;
        ctx.strokeStyle = `rgb(${WHITE})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(x, y + h / 2 + 4);
        ctx.stroke();
        ctx.globalAlpha = 0.15 + 0.85 * e;
        ctx.drawImage(f.img, x - w / 2, y - h / 2, w, h);
        ctx.strokeRect(x - w / 2 - 3.5, y - h / 2 - 3.5, w + 7, h + 7);
        if (k > 0.28 && k < 0.74) { // measuring cross
          const m = (k - 0.28) / 0.46;
          ctx.globalAlpha = 0.9;
          ctx.strokeStyle = `rgb(${S.mode === "faces" ? WARM : ICE})`;
          ctx.beginPath();
          ctx.moveTo(x - w / 2 + w * m, y - h / 2);
          ctx.lineTo(x - w / 2 + w * m, y + h / 2);
          ctx.moveTo(x - w / 2, y - h / 2 + h * m);
          ctx.lineTo(x + w / 2, y - h / 2 + h * m);
          ctx.stroke();
        }
        if (k > 0.97 && !pinged.has(f.n)) { pinged.add(f.n); pings.push({ i: f.star, at: t }); }
      }
      ctx.globalCompositeOperation = "lighter";
      for (let i = pings.length - 1; i >= 0; i--) {
        const age = (t - pings[i].at) / 0.8;
        if (age > 1) { pings.splice(i, 1); continue; }
        const [x, y] = pos(Math.min(n - 1, pings[i].i));
        ctx.globalAlpha = 1 - age;
        ctx.drawImage(star, x - 18, y - 18, 36, 36);
        ctx.strokeStyle = `rgb(${WHITE})`;
        ctx.beginPath();
        ctx.ellipse(x, y, 6 + age * 34, (6 + age * 34) * TILT, 0, 0, TAU);
        ctx.stroke();
      }
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1;
    },
  };
  return scene;
}
