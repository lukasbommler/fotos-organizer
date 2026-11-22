"""Download the evaluation photos of tests/fixtures/testset.json (development only).

Usage:  uv run python scripts/fetch_testset.py [--dest DIR]

Freely licensed photos from Wikimedia Commons, 1280-px renditions, into
%LOCALAPPDATA%\\FotoDuenner-dev\\testset\\ (never into the repo, D8). The licence of every file is
read from Commons and written to sources.json next to the photos. Synthetic blurry copies
(``synblur_*``) and screenshots of this app (``synscreen_*``) are derived locally.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.paths import dev_models_dir

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "testset.json"
API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = {"User-Agent": "fotoduenner-dev-testset/0.1"}
FREE = ("CC0", "Public domain", "CC BY", "CC BY-SA")
WIDTH = 1280


def default_dest() -> Path:
    return dev_models_dir().parent / "testset"


def fetch(url: str, attempts: int = 6) -> bytes:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=USER_AGENT)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            wait = int(exc.headers.get("Retry-After") or 0) or 10 * 2**attempt
            host = urllib.parse.urlsplit(url).netloc
            print(f"  rate limited by {host}, waiting {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def image_info(title: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": f"File:{title}",
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": WIDTH,
            "format": "json",
        }
    )
    page = next(iter(json.loads(fetch(f"{API}?{query}"))["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise LookupError(f"not found on Commons: {title}")
    return page["imageinfo"][0]


THUMB_HOST = "https://thumb.wikimedia.org/wikipedia/commons/thumb/"
STANDARD_WIDTHS = (1280, 960, 500)


def thumb_url(info: dict) -> str:
    """Thumbnail on the dedicated thumb host (upload.wikimedia.org throttles scripts hard).

    Files narrower than 1280 px get the largest standard width below their own width.
    """
    original = info["url"].split("?", 1)[0]
    path = original.split("/wikipedia/commons/", 1)[1]  # e.g. d/dc/Name.jpg
    name = path.rsplit("/", 1)[1]
    width = next((w for w in STANDARD_WIDTHS if w < info["width"]), None)
    if width is None:
        return original
    return f"{THUMB_HOST}{path}/{width}px-{name}"


def motion_blur(img: Image.Image, length: int) -> Image.Image:
    import cv2

    kernel = np.zeros((length, length), np.float32)
    kernel[length // 2, :] = 1.0 / length
    arr = cv2.filter2D(np.asarray(img), -1, kernel)
    return Image.fromarray(arr)


def derive(dest: Path, items: list[dict]) -> list[dict]:
    """Blurred copies of sharp negatives + screenshots of this app's mock-ups."""
    derived: list[dict] = []
    sharp = [i for i in items if i["group"] == "neg"][:12]
    for n, item in enumerate(sharp):
        src = dest / item["file"]
        if not src.exists():
            continue
        img = Image.open(src).convert("RGB")
        blurred = (
            img.filter(ImageFilter.GaussianBlur(4 + n % 3 * 2))
            if n % 2 == 0
            else motion_blur(img, 25 + n % 3 * 10)
        )
        name = f"synblur_{n:02d}.jpg"
        blurred.save(dest / name, quality=90)
        derived.append({"file": name, "group": "blurry", "title": f"derived from {item['file']}"})
    for n, png in enumerate(sorted((ROOT / "docs" / "mockups").glob("*.png"))):
        name = f"synscreen_{n:02d}.jpg"
        Image.open(png).convert("RGB").save(dest / name, quality=90)
        derived.append({"file": name, "group": "screen", "title": f"app screenshot {png.name}"})
    return derived


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=default_dest())
    args = parser.parse_args()
    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))["items"]
    sources_path = dest / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    for item in items:
        target = dest / item["file"]
        if target.exists() and item["file"] in sources:
            continue
        try:
            info = image_info(item["title"])
        except LookupError as exc:
            print(f"  skip   {exc}")
            continue
        licence = info["extmetadata"].get("LicenseShortName", {}).get("value", "?")
        if not licence.startswith(FREE):
            print(f"  skip   {item['title']} (licence {licence})")
            continue
        try:
            target.write_bytes(fetch(thumb_url(info), attempts=2))
        except (RuntimeError, OSError) as exc:  # small originals live on the throttled host
            print(f"  skip   {item['title']} ({exc})")
            continue

        sources[item["file"]] = {
            "title": item["title"],
            "licence": licence,
            "source": info["descriptionurl"],
        }
        print(f"  fetch  {item['file']}  {licence}  {item['title'][:60]}")
        sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=1), "utf-8")
        time.sleep(2)  # be gentle with the Commons API
    derived = derive(dest, items)
    (dest / "derived.json").write_text(json.dumps(derived, indent=1), encoding="utf-8")
    print(f"{len(sources)} downloaded, {len(derived)} derived -> {dest}")


if __name__ == "__main__":
    main()
