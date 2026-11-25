// Design 2 "Scanner": photos travel as holograms along a curved line through a light gate and
// come out as real pictures. The arch of the gate carries the progress.
import { clamp, easeOut, glowSprite, lerp } from "./util.js";

const GOLD = "255,176,46";
const PALE = "255,232,196";
const COOL = "122,220,255";
const SLICES = 14;
const SPACING = 2.0;
const CARDS = 9;

export function holo() {
  const glow = glowSprite(GOLD);
  const spark = glowSprite(PALE, 32);
  const cards = Array.from({ length: CARDS }, (_, i) => ({ x: (i - 4) * SPACING, img: null, tint: null }));
  const sparks = [];
  let floor = 0;
  let used = -1; // newest photo already on a card

  function tinted(img) {
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const g = c.getContext("2d");
    g.filter = "grayscale(1) contrast(1.2)";
    g.drawImage(img, 0, 0);
    g.filter = "none";
    g.globalCompositeOperation = "multiply";
    g.fillStyle = `rgb(${GOLD})`;
    g.fillRect(0, 0, c.width, c.height);
    g.globalCompositeOperation = "destination-out"; // hologram lines
    g.globalAlpha = 0.45;
    for (let y = 0; y < c.height; y += 4) g.fillRect(0, y, c.width, 1.4);
    return c;
  }

  const scene = {
    photoLife: 6, // cards keep their own pictures
    photoGap: 500,
    draw(ctx, S) {
      const cx = S.w * 0.5;
      const narrow = S.w < 900;
      const horizon = S.h * (narrow ? 0.5 : 0.34);
      const f = S.h * (narrow ? 0.5 : 0.76);
      const cam = 0.98; // height of the line above the floor
      const t = S.t;
      const burst = S.mode === "done" ? clamp(S.since / 1.2) : 0;
      const depth = (x) => 3.1 + 0.1 * x * x;
      const sx = (x, z) => cx + (x * f) / z;
      const sy = (y, z) => horizon + ((cam - y) * f) / z; // y: height above the floor

      // floor grid flowing towards the viewer
      floor = (floor + S.dt * (0.25 + S.energy * 0.6)) % 1;
      ctx.lineWidth = 1;
      for (let i = 0; i < 16; i++) {
        const z = 16 - i - floor;
        if (z < 0.9) continue;
        const scan = S.mode === "scan" && Math.abs(z - (14 - ((S.since * 7) % 14))) < 0.8;
        ctx.globalAlpha = clamp((16 - z) / 16) * (scan ? 1 : 0.28);
        ctx.strokeStyle = `rgb(${scan ? PALE : GOLD})`;
        const y = sy(0, z);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(S.w, y);
        ctx.stroke();
      }
      ctx.strokeStyle = `rgb(${GOLD})`;
      ctx.globalAlpha = 0.16;
      ctx.beginPath();
      for (let i = -14; i <= 14; i++) {
        ctx.moveTo(sx(i * 0.9, 16), sy(0, 16));
        ctx.lineTo(sx(i * 0.9, 0.9), sy(0, 0.9));
      }
      ctx.stroke();
      ctx.globalAlpha = 0.5;
      ctx.globalCompositeOperation = "lighter";
      ctx.drawImage(glow, cx - S.w * 0.4, horizon - S.h * 0.1, S.w * 0.8, S.h * 0.2);
      ctx.globalCompositeOperation = "source-over";

      // move the cards, hand out new photos at the entrance
      const speed = S.mode === "done" ? 5 : 0.25 + S.energy * 1.3;
      for (const c of cards) {
        c.x += S.dt * speed;
        if (c.x > 4.5 * SPACING) {
          c.x -= CARDS * SPACING;
          c.img = c.tint = null;
        }
      }
      const fresh = S.photos.filter((p) => p.n > used);
      for (const p of fresh) { // next empty card that has not reached the gate yet
        const card = cards.filter((c) => !c.img && c.x < -0.9).sort((a, b) => b.x - a.x)[0];
        if (!card) break;
        card.img = p.img;
        card.tint = tinted(p.img);
        used = p.n;
      }

      const hw = 0.72;
      const hh = 0.54;
      const mid = 1.0;
      const order = [...cards].sort((a, b) => depth(b.x) - depth(a.x));
      for (const c of order) {
        const far = clamp(1 - (Math.abs(c.x) - 3.7) / 1.1);
        if (far <= 0) continue;
        const top = [];
        const bottom = [];
        for (let i = 0; i <= SLICES; i++) {
          const x = c.x - hw + (2 * hw * i) / SLICES;
          const z = depth(x);
          top.push([sx(x, z), sy(mid + hh, z)]);
          bottom.push([sx(x, z), sy(mid - hh, z)]);
        }
        for (let i = 0; i < SLICES; i++) {
          const x = c.x - hw + (2 * hw * (i + 0.5)) / SLICES;
          const passed = x > 0;
          const [x0, y0] = top[i];
          const w = top[i + 1][0] - x0 + 0.6;
          const h = bottom[i][1] - y0;
          const img = passed ? c.img : c.tint;
          if (img) {
            ctx.globalAlpha = far * (passed ? 1 : 0.6);
            ctx.drawImage(img, (img.width * i) / SLICES, 0, img.width / SLICES, img.height, x0, y0, w, h);
          } else {
            ctx.globalAlpha = far * 0.1;
            ctx.fillStyle = `rgb(${GOLD})`;
            ctx.fillRect(x0, y0, w, h);
          }
        }
        ctx.globalAlpha = far * (c.img ? 0.95 : 0.4);
        ctx.strokeStyle = `rgb(${c.x > 0 && c.img ? PALE : GOLD})`;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        top.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        [...bottom].reverse().forEach(([x, y]) => ctx.lineTo(x, y));
        ctx.closePath();
        ctx.stroke();
        // light line on the floor the hologram stands on
        ctx.globalAlpha = far * (c.img ? 0.75 : 0.3);
        ctx.beginPath();
        for (let i = 0; i <= SLICES; i++) {
          const x = c.x - hw + (2 * hw * i) / SLICES;
          const z = depth(x);
          if (i) ctx.lineTo(sx(x, z), sy(0, z)); else ctx.moveTo(sx(x, z), sy(0, z));
        }
        ctx.stroke();
        ctx.globalAlpha = far * 0.16;
        ctx.beginPath();
        for (const i of [0, SLICES]) {
          const x = c.x - hw + (2 * hw * i) / SLICES;
          ctx.moveTo(bottom[i][0], bottom[i][1]);
          ctx.lineTo(sx(x, depth(x)), sy(0, depth(x)));
        }
        ctx.stroke();
        if (S.mode === "faces" && c.img && Math.abs(c.x) < 1.6) { // second pass: closer look
          const x0 = top[4][0];
          const w = top[10][0] - x0;
          const y0 = lerp(top[7][1], bottom[7][1], 0.22);
          const k = 10;
          ctx.strokeStyle = `rgb(${COOL})`;
          ctx.globalAlpha = 0.6 + 0.4 * Math.sin(t * 8);
          ctx.lineWidth = 2;
          ctx.beginPath();
          for (const [qx, qy, dx, dy] of [[x0, y0, 1, 1], [x0 + w, y0, -1, 1], [x0, y0 + w, 1, -1], [x0 + w, y0 + w, -1, -1]]) {
            ctx.moveTo(qx + dx * k, qy);
            ctx.lineTo(qx, qy);
            ctx.lineTo(qx, qy + dy * k);
          }
          ctx.stroke();
        }
        if (Math.abs(c.x) < hw && sparks.length < 70 && !S.saving) {
          sparks.push({ x: cx, y: lerp(top[7][1], bottom[7][1], Math.random()), vx: 40 + Math.random() * 110, vy: (Math.random() - 0.5) * 70, life: 0.7 });
        }
      }

      // the gate: light sheet, arch of progress ticks
      const z0 = depth(0);
      const base = sy(0, z0);
      const head = sy(mid + hh + 0.42, z0);
      const half = (0.5 * f) / z0;
      const warm = S.mode === "faces" ? COOL : GOLD;
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = 0.5 + 0.16 * Math.sin(t * 5) + burst * 0.5;
      ctx.drawImage(glow, cx - 46, head - 40, 92, base - head + 80);
      ctx.globalAlpha = 0.95;
      ctx.fillStyle = `rgb(${PALE})`;
      ctx.fillRect(cx - 1, head, 2, base - head);
      const beam = head + (0.5 + 0.5 * Math.sin(t * 2.6 * (0.5 + S.energy))) * (base - head);
      ctx.globalAlpha = 0.8;
      ctx.fillStyle = `rgb(${warm})`;
      ctx.fillRect(cx - half * 0.9, beam, half * 1.8, 1.5);
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i];
        s.life -= S.dt;
        s.x += s.vx * S.dt;
        s.y += s.vy * S.dt;
        if (s.life <= 0) { sparks.splice(i, 1); continue; }
        ctx.globalAlpha = s.life;
        ctx.drawImage(spark, s.x - 5, s.y - 5, 10, 10);
      }
      ctx.globalCompositeOperation = "source-over";
      const n = 90;
      const path = (k, off) => { // k 0..1 along left post, arch, right post
        const post = 0.3;
        const r = half + off;
        if (k < post) return [cx - r, lerp(base, head + half, k / post)];
        if (k > 1 - post) return [cx + r, lerp(head + half, base, (k - 1 + post) / post)];
        const a = Math.PI + ((k - post) / (1 - 2 * post)) * Math.PI;
        return [cx + Math.cos(a) * r, head + half + Math.sin(a) * r];
      };
      for (const on of [false, true]) {
        ctx.beginPath();
        for (let i = 0; i <= n; i++) {
          const k = i / n;
          const wave = S.mode !== "analyze" && S.mode !== "faces" && S.mode !== "done" && ((k - t * 0.2) % 1 + 1) % 1 > 0.94;
          if ((k <= S.p && S.p > 0.001) !== on && !(wave && on)) continue;
          const [ax, ay] = path(k, 8);
          const [bx, by] = path(k, on ? 22 : 15);
          ctx.moveTo(ax, ay);
          ctx.lineTo(bx, by);
        }
        ctx.globalAlpha = on ? 1 : 0.35;
        ctx.strokeStyle = `rgb(${on ? PALE : GOLD})`;
        ctx.lineWidth = on ? 2.4 : 1;
        ctx.stroke();
      }
      ctx.globalAlpha = 0.7;
      ctx.strokeStyle = `rgb(${GOLD})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let i = 0; i <= 60; i++) { const [x, y] = path(i / 60, 4); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
      ctx.stroke();
      if (burst > 0) {
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = (1 - burst) * 0.9;
        const g = easeOut(burst) * S.w;
        ctx.drawImage(glow, cx - g, (head + base) / 2 - g * 0.5, g * 2, g);
        ctx.globalCompositeOperation = "source-over";
      }
      ctx.globalAlpha = 1;
    },
  };
  return scene;
}
