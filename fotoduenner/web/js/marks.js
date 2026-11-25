// Thin line icons and the sharpness gauge (D16: no emoji, no sparkles).

const SVG = "http://www.w3.org/2000/svg";

function svg(cls, d, viewBox = "0 0 100 100", stretch = true) {
  const el = document.createElementNS(SVG, "svg");
  el.setAttribute("class", cls);
  el.setAttribute("viewBox", viewBox);
  if (stretch) el.setAttribute("preserveAspectRatio", "none");
  el.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", d);
  el.append(path);
  return el;
}

const ICONS = {
  person: "M50 44a14 14 0 1 0 0-28a14 14 0 0 0 0 28M22 86c2-18 14-28 28-28s26 10 28 28",
  hand: "M34 86V46M34 46V24a6 6 0 0 1 12 0v20M46 42V18a6 6 0 0 1 12 0v24M58 44V24a6 6 0 0 1 12 0v30c0 18-10 32-26 32",
  bracket: "M14 50h72M50 20v60M26 32v36M74 32v36",
  pano: "M10 36c26-8 54-8 80 0v28c-26-8-54-8-80 0z",
  copy: "M30 22h44v52H30zM20 32v52h44",
  eyesClosed: "M12 46c10 14 24 20 38 20s28-6 38-20M30 60l-6 10M50 66v12M70 60l6 10",
  star: "M50 12l11 24 26 3-19 18 5 26-23-13-23 13 5-26-19-18 26-3z",
  chevron: "M30 40l20 20 20-20",
};

export function icon(name, title) {
  const el = svg(`icon icon-${name}`, ICONS[name], "0 0 100 100", false);
  if (title) {
    const t = document.createElementNS(SVG, "title");
    t.textContent = title;
    el.prepend(t);
    el.removeAttribute("aria-hidden");
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", title);
  }
  return el;
}

/** Three-step sharpness gauge (04 §1.4), from the album-relative sharpness 0..1. */
export function sharpGauge(sharp) {
  const level = sharp >= 0.66 ? 3 : sharp >= 0.33 ? 2 : 1;
  const words = ["", "unscharf", "mittel", "scharf"];
  const el = document.createElement("span");
  el.className = `gauge g${level}`;
  el.title = `Schärfe: ${words[level]}`;
  for (let i = 1; i <= 3; i++) el.append(document.createElement("i"));
  return el;
}
