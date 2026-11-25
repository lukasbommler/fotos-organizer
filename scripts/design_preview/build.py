"""Bundle the real frontend with an invented album into one self-contained page.

    uv run python scripts/design_preview/build.py OUT_DIR
Only for showing the program; the page contains no photos and no data of the user.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent.parent / "fotoduenner" / "web"
JS = WEB / "js"
TITLE = "FotoDünner Reaktor"
IMPORT = re.compile(r'^import \{([^}]*)\} from "([^"]+)";\n', re.MULTILINE)
EXPORT = re.compile(r"^export (?:async )?(?:const|let|class|function) (\w+)", re.MULTILINE)

JUMP = """
// preview: jump bar to the screens
window.__preview = { startScreen, reviewScreen, demoOpen: () => openAlbum("demo", errorLine()) };
const slot = h("span.designs", { role: "group", "aria-label": "Bildschirme" });
document.querySelector(".bar .readout").before(slot);
const steps = [
  ["Start", () => startScreen()],
  ["Einlesen", () => openAlbum("demo", errorLine())],
  ["Analyse", async () => progressScreen(await api.open())],
  ["Übersicht", () => reviewScreen("demo")],
];
const buttons = steps.map(([label, go]) =>
  h("button.jump", { type: "button", onclick: () => go() }, label));
slot.replaceChildren(...buttons);
const mark = () => {
  const screens = ["hud-start", "hud-check", "hud-work", "review"];
  const k = Math.max(0, screens.findIndex((name) => main.classList.contains(name)));
  buttons.forEach((b, i) => b.setAttribute("aria-current", String(i === k)));
};
new MutationObserver(mark).observe(main, { attributes: true, attributeFilter: ["class"] });
mark();
"""


def module_source(path: Path) -> str:
    if path == JS / "api.js":
        return (HERE / "mock_api.js").read_text(encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    if path == JS / "viewer.js":  # storage may be refused inside a frame
        text = text.replace('sessionStorage.getItem("fd-debug") === "1"', "false")
        text = re.sub(r"\s*sessionStorage\.setItem\([^;]*;", "", text)
    if path == JS / "app.js":
        text += JUMP
    return text


def bundle(entry: Path) -> str:
    order: list[Path] = []
    sources: dict[Path, str] = {}

    def visit(path: Path, trail: tuple[Path, ...]) -> None:
        assert path not in trail, f"import cycle: {path}"
        if path in sources:
            return
        text = module_source(path)
        sources[path] = text
        for match in IMPORT.finditer(text):
            visit((path.parent / match[2]).resolve(), (*trail, path))
        order.append(path)

    visit(entry.resolve(), ())
    out = ["const __m = {};"]
    for path in order:
        text = sources[path]
        key = path.relative_to(JS).as_posix()

        def imports(match: re.Match[str], base: Path = path) -> str:
            target = (base.parent / match[2]).resolve().relative_to(JS).as_posix()
            return f'const {{{match[1]}}} = __m["{target}"];\n'

        names = EXPORT.findall(text)
        body = IMPORT.sub(imports, text)
        body = re.sub(r"^export ", "", body, flags=re.MULTILINE)
        kind = "async " if path == entry.resolve() else ""
        out.append(
            f'__m["{key}"] = ({kind}() => {{\n{body}\nreturn {{ {", ".join(names)} }};\n}})();'
        )
    code = "\n".join(out)
    assert "</script" not in code
    return code


def styles() -> str:
    css = "\n".join(
        (WEB / name).read_text(encoding="utf-8") for name in ("styles.css", "review.css", "hud.css")
    )

    def font(match: re.Match[str]) -> str:
        data = base64.b64encode((WEB / match[1]).read_bytes()).decode()
        return f'url("data:font/woff2;base64,{data}")'

    css = re.sub(r'url\("(fonts/[^"]+)"\)', font, css)
    return css + (HERE / "preview.css").read_text(encoding="utf-8")


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    index = (WEB / "index.html").read_text(encoding="utf-8")
    body = index[index.index("<body>") + 6 : index.index("</body>")]
    body = re.sub(r"\s*<script[^>]*></script>", "", body).replace(" hidden></canvas>", "></canvas>")
    # statements about the program that are not true for a preview page
    body = body.replace("127.0.0.1", "Entwurf").replace(
        "Ihre Fotos verlassen diesen Rechner nicht", "Gemalte Beispielbilder"
    )
    code, css = bundle(JS / "app.js"), styles()
    page = (
        f"<title>{TITLE}</title>\n<style>\n:root {{ color-scheme: dark; }}\n{css}\n</style>\n"
        f"{body}\n<script>\n{code}\n</script>\n"
    )
    (out / "fotoduenner-reaktor.html").write_text(page, encoding="utf-8")
    print(f"{len(page) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
