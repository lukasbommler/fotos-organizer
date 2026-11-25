// The three designs (D21): name, switch in the header, shared animated stage. The choice is kept by
// the program (the local port changes with every start, so localStorage would forget it).
import { api } from "./api.js";
import { HudStage } from "./hud/stage.js";
import { h } from "./util.js";

export const DESIGNS = [
  ["orbit", "Reaktor"],
  ["holo", "Scanner"],
  ["nova", "Sternkarte"],
];

let current = "orbit";
export const stage = new HudStage(document.getElementById("hud"));

function paint() {
  document.documentElement.dataset.design = current;
  stage.setDesign(current);
  for (const b of document.querySelectorAll(".design-opt")) b.setAttribute("aria-pressed", String(b.dataset.design === current));
}

export function setDesign(name, { save = true } = {}) {
  if (!DESIGNS.some(([key]) => key === name)) return;
  current = name;
  paint();
  if (save) api.setPrefs({ design: name }).catch(() => {});
}

export async function initDesign(slot) {
  slot.replaceChildren(h("span.design-label", {}, "Design"),
    ...DESIGNS.map(([key, label], i) => h("button.design-opt", { type: "button", title: `Design ${i + 1}: ${label}`, dataset: { design: key },
      onclick: () => setDesign(key) }, label)));
  try { current = (await api.prefs()).design ?? current; } catch { /* default design */ }
  if (!DESIGNS.some(([key]) => key === current)) current = "orbit";
  paint();
}

/** Simple painted landscapes for the animation preview (no real photos involved). */
export function samplePhoto(k) {
  const c = document.createElement("canvas");
  c.width = 320;
  c.height = 240;
  const g = c.getContext("2d");
  const hue = (k * 47) % 360;
  const sky = g.createLinearGradient(0, 0, 0, 170);
  sky.addColorStop(0, `hsl(${200 + (k % 5) * 14} 60% ${25 + (k % 4) * 12}%)`);
  sky.addColorStop(1, `hsl(${30 + (hue % 40)} 70% 70%)`);
  g.fillStyle = sky;
  g.fillRect(0, 0, 320, 240);
  g.fillStyle = "rgba(255,250,230,.9)";
  g.beginPath();
  g.arc(60 + ((k * 71) % 200), 60 + (k % 3) * 22, 20, 0, 7);
  g.fill();
  for (let layer = 0; layer < 3; layer++) {
    g.fillStyle = `hsl(${150 + layer * 25 + (k % 6) * 8} ${28 - layer * 5}% ${42 - layer * 13}%)`;
    g.beginPath();
    g.moveTo(0, 240);
    for (let x = 0; x <= 320; x += 10) g.lineTo(x, 130 + layer * 32 + Math.sin(x / (34 + layer * 12) + k * 1.3 + layer) * (22 - layer * 5));
    g.lineTo(320, 240);
    g.fill();
  }
  return c;
}
