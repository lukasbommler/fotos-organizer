"""API: session protection (T12) and the M2 flow open → analyse → view."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixtures.make_fixtures import make_jpeg

from fotoduenner.api.security import COOKIE_NAME, POST_HEADER
from fotoduenner.api.server import create_app
from fotoduenner.api.state import AppState
from fotoduenner.config import Config

TOKEN = "test-token-123"
HOST = "testserver"
POST = {POST_HEADER: "1"}


def _client(state: AppState | None = None) -> TestClient:
    app = create_app(state or AppState(Config(analysis_workers=0)), TOKEN, {HOST})
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    c = _client()
    assert c.get(f"/?t={TOKEN}").status_code == 200  # follows the redirect, keeps the cookie
    return c


def test_token_becomes_strict_cookie() -> None:
    c = _client()
    first = c.get(f"/?t={TOKEN}", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/"
    cookie = first.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert COOKIE_NAME in c.cookies


def test_requests_without_session_are_rejected() -> None:
    c = _client()
    assert c.get("/").status_code == 403
    assert c.get("/?t=wrong").status_code == 403
    assert c.get("/api/version").status_code == 401
    assert c.get("/static/styles.css").status_code == 200  # static assets are not secret


def test_post_needs_custom_header(client: TestClient) -> None:
    assert client.get("/api/version").status_code == 200
    assert client.post("/api/shutdown").status_code == 403
    assert client.post("/api/shutdown", headers=POST).status_code == 200


def test_foreign_host_is_rejected(client: TestClient) -> None:
    assert client.get("/api/version", headers={"host": "evil.example:80"}).status_code == 403


def test_pick_folder_without_window(client: TestClient) -> None:
    res = client.post("/api/pick-folder", headers=POST)
    assert res.status_code == 501
    assert "Programmfenster" in res.json()["detail"]


def test_open_missing_folder(client: TestClient, tmp_path: Path) -> None:
    res = client.post("/api/album/open", json={"path": str(tmp_path / "nope")}, headers=POST)
    assert res.status_code == 404
    assert res.json()["detail"] == "Ordner nicht gefunden."


def _wait_done(client: TestClient, album_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        view = client.get(f"/api/album/{album_id}/view").json()
        if view["state"] != "running":
            return view
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_open_analyse_view_flow(client: TestClient, tmp_path: Path) -> None:
    album = tmp_path / "Kroatien 2025"
    make_jpeg(album / "b.jpg", seed=2, taken="2025:08:01 12:00:00")
    make_jpeg(album / "a.jpg", seed=1, taken="2025:08:01 11:00:00")
    make_jpeg(album / "Tag 2" / "c.jpg", seed=3, taken="2025:08:02 09:00:00")
    (album / "video.mp4").write_bytes(b"x")
    (album / "kaputt.jpg").write_bytes(b"\xff\xd8 broken")

    pre = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()
    assert pre["photos"] == 4 and pre["ignored"] == 1 and pre["pending"] == 4
    assert pre["few"] is True and pre["offline"] == 0
    assert pre["drive"] in ("fixed", "external", "network", "unknown")
    album_id = pre["album_id"]

    assert client.post(f"/api/album/{album_id}/analyze", headers=POST).status_code == 200
    view = _wait_done(client, album_id)
    assert view["state"] == "done"
    names = [p["name"] for p in view["photos"]]
    assert names[:3] == ["a.jpg", "b.jpg", "c.jpg"]  # capture order
    assert [p["n"] for p in view["photos"]] == [1, 2, 3, 4]
    broken = next(p for p in view["photos"] if p["name"] == "kaputt.jpg")
    assert broken["status"] == "error"

    first = view["photos"][0]
    thumb = client.get(f"/api/album/{album_id}/photo/{first['id']}/thumb?size=s&v={first['v']}")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert "immutable" in thumb.headers["cache-control"]
    assert client.get(f"/api/album/{album_id}/photo/{first['id']}/thumb?size=l").status_code == 200
    assert client.get(f"/api/album/{album_id}/photo/{broken['id']}/thumb").status_code == 404
    assert client.get(f"/api/album/{album_id}/photo/99999/thumb").status_code == 404

    info = client.get(f"/api/album/{album_id}/photo/{first['id']}/info").json()
    assert info["name"] == "a.jpg" and info["taken_at_source"] == "exif"
    feats = info["features"]  # raw values for the debug view (key D)
    assert feats["sharpness_peak"] > 0 and 0 <= feats["q_exposure"] <= 1
    assert set(feats["zeroshot"]) == {"person_back", "screenshot", "document_sign", "blurry"}
    assert feats["faces"] == [] and len(feats["phash"]) == 16
    assert client.get(f"/api/album/{album_id}/photo/{broken['id']}/info").json()["features"] is None

    again = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()
    assert again["pending"] == 0 and again["cached"] == 4 and again["faces_pending"] == 0

    recent = client.get("/api/recent").json()
    assert recent[0]["name"] == "Kroatien 2025" and recent[0]["done"] == 4


def test_events_stream_reports_progress(client: TestClient, tmp_path: Path) -> None:
    album = tmp_path / "Album"
    for i in range(3):
        make_jpeg(album / f"{i}.jpg", seed=i)
    album_id = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()[
        "album_id"
    ]
    client.post(f"/api/album/{album_id}/analyze", headers=POST)
    events = []
    with client.stream("GET", f"/api/album/{album_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["state"] == "done"
    finished = [pid for e in events for pid in e["finished"]]
    assert sorted(finished) == sorted(
        p["id"] for p in client.get(f"/api/album/{album_id}/view").json()["photos"]
    )


def test_photo_of_unopened_album(client: TestClient) -> None:
    assert client.get("/api/album/deadbeef/photo/1/thumb").status_code == 404


def test_review_proposals_decisions_settings(client: TestClient, tmp_path: Path) -> None:
    album = tmp_path / "Album"
    make_jpeg(album / "a.jpg", seed=1, taken="2025:08:01 11:00:00")
    make_jpeg(album / "b.jpg", seed=2, taken="2025:08:01 12:00:00")
    (album / "Kopie").mkdir()
    (album / "Kopie" / "a.jpg").write_bytes((album / "a.jpg").read_bytes())
    album_id = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()[
        "album_id"
    ]
    client.post(f"/api/album/{album_id}/analyze", headers=POST)
    view = _wait_done(client, album_id)
    review = view["review"]
    ids = {p["rel_path"]: p["id"] for p in view["photos"]}
    copy_id, orig_id = ids["Kopie/a.jpg"], ids["a.jpg"]
    states = {d["id"]: d for d in review["decisions"]}
    assert states[copy_id]["keep"] is False and states[copy_id]["ref"] == orig_id
    assert states[copy_id]["reason"] == "Exakte Kopie von a.jpg."
    assert states[orig_id]["keep"] and states[ids["b.jpg"]]["keep"]
    assert review["duplicates"] == [{"id": copy_id, "of": orig_id}]
    assert review["stats"]["mode"] == "clean" and review["stats"]["reject_duplicate"] == 1
    grouped = set(review["singles"]) | {i for g in review["groups"] for i in g["ids"]}
    assert grouped == {orig_id, ids["b.jpg"]}  # exact copies are in no group

    url = f"/api/album/{album_id}/decision"
    diff = client.post(url, json={"photo_id": copy_id, "manual": "keep"}, headers=POST).json()
    assert [c["id"] for c in diff["changed"]] == [copy_id]  # a click changes only this photo
    assert diff["changed"][0]["keep"] and diff["changed"][0]["manual"] == "keep"
    assert diff["stats"]["keep"] == 3
    back = client.post(url, json={"photo_id": copy_id, "manual": None}, headers=POST).json()
    assert back["changed"][0]["keep"] is False and back["changed"][0]["manual"] is None
    assert (
        client.post(url, json={"photo_id": 999, "manual": "keep"}, headers=POST).status_code == 404
    )

    settings = f"/api/album/{album_id}/settings"
    res = client.post(settings, json={"mode": "quota", "keep_target": 0.95}, headers=POST).json()
    assert res["stats"]["mode"] == "quota" and res["stats"]["keep_target"] == 0.9  # clamped
    assert "groups" not in res
    res = client.post(settings, json={"level": 4}, headers=POST).json()
    assert res["stats"]["level"] == 4 and "groups" in res
    # stored per album (A4): a new session sees the same settings and the manual decision
    client.post("/api/album/open", json={"path": str(album)}, headers=POST)
    client.post(url, json={"photo_id": orig_id, "manual": "reject"}, headers=POST)
    again_view = client.get(f"/api/album/{album_id}/view").json()
    again = again_view["review"]
    assert again["stats"]["mode"] == "quota" and again["stats"]["level"] == 4
    assert {d["id"]: d for d in again["decisions"]}[orig_id]["reason"] == "Von Ihnen aussortiert."

    # group action: several photos at once
    both = [orig_id, ids["b.jpg"]]
    res = client.post(url, json={"photo_ids": both, "manual": "keep"}, headers=POST).json()
    assert {c["id"] for c in res["changed"]} == set(both) and res["stats"]["reject_manual"] == 0
    assert all(c["manual"] == "keep" and c["keep"] for c in res["changed"])
    assert client.post(url, json={"manual": "keep"}, headers=POST).status_code == 422

    full = client.get(f"/api/album/{album_id}/photo/{orig_id}/full")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    from io import BytesIO

    from PIL import Image

    assert Image.open(BytesIO(full.content)).size == (640, 480)
    assert next(p for p in again_view["photos"] if p["id"] == orig_id)["size"] > 0


def _wait_job(client: TestClient, album_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = client.get(f"/api/album/{album_id}/apply/status").json()
        if status["job"] and status["job"]["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_apply_hold_rethin_and_undo(client: TestClient, tmp_path: Path) -> None:
    album = tmp_path / "Album"
    make_jpeg(album / "a.jpg", seed=1, taken="2025:08:01 11:00:00")
    make_jpeg(album / "b.jpg", seed=2, taken="2025:08:01 12:00:00")
    (album / "Kopie").mkdir()
    (album / "Kopie" / "a.jpg").write_bytes((album / "a.jpg").read_bytes())
    (album / "Kopie" / "a.xmp").write_text("<x/>", encoding="utf-8")
    album_id = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()[
        "album_id"
    ]
    client.post(f"/api/album/{album_id}/analyze", headers=POST)
    view = _wait_done(client, album_id)
    assert view["apply"]["applied"] is False and view["apply"]["last"] is None
    base = f"/api/album/{album_id}"

    pre = client.post(f"{base}/apply/preview", headers=POST).json()
    assert pre["photos"] == 1 and pre["files"] == 2 and pre["by_reason"]["duplicate"] == 1
    assert pre["blocking"] == [] and pre["count_before"] == 3 and pre["bytes"] > 0
    wrong = client.post(f"{base}/apply", json={"photo_ids": [12345]}, headers=POST)
    assert wrong.status_code == 409 and "geändert" in wrong.json()["detail"]
    started = client.post(f"{base}/apply", json={"photo_ids": pre["photo_ids"]}, headers=POST)
    assert started.status_code == 200
    status = _wait_job(client, album_id)
    assert status["job"]["state"] == "done" and status["job"]["report"]["photos"] == 1
    assert status["job"]["report"]["files"] == 2
    assert (album / "_Aussortiert" / "Kopie" / "a.xmp").is_file()

    # T2: the album is "übernommen", everything left is kept – also after reopening
    view = client.get(f"{base}/view").json()
    assert [p["rel_path"] for p in view["photos"]] == ["a.jpg", "b.jpg"]
    stats = view["review"]["stats"]
    assert stats["applied"] and stats["hold"] and stats["count_before"] == 3
    assert stats["keep"] == stats["total"] == 2
    assert view["apply"]["last"]["photos"] == 1
    client.post(f"{base}/settings", json={"mode": "quota", "keep_target": 0.3}, headers=POST)
    assert client.get(f"{base}/view").json()["review"]["stats"]["keep"] == 2
    again = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()
    assert again["apply"]["applied"] and again["photos"] == 2
    assert client.get("/api/recent").json()[0]["applied"] is True
    assert client.post(f"{base}/open-folder", headers=POST).status_code == 501

    rethin = client.post(f"{base}/rethin", headers=POST).json()
    assert rethin["stats"]["hold"] is False and rethin["stats"]["applied"] is True
    client.post(f"{base}/settings", json={"mode": "clean"}, headers=POST)

    assert client.post(f"{base}/undo", json={}, headers=POST).status_code == 200
    status = _wait_job(client, album_id)
    assert status["job"]["state"] == "done" and status["job"]["report"]["photos"] == 1
    assert not (album / "_Aussortiert").exists()
    view = client.get(f"{base}/view").json()
    assert len(view["photos"]) == 3 and view["apply"]["applied"] is False
    states = {d["id"]: d for d in view["review"]["decisions"]}
    copy_id = next(p["id"] for p in view["photos"] if p["rel_path"] == "Kopie/a.jpg")
    assert states[copy_id]["keep"] is False  # the proposal is back as before
    assert client.post(f"{base}/undo", headers=POST).status_code == 409


def test_please_check_photo_can_be_confirmed(client: TestClient, tmp_path: Path) -> None:
    """Keeping a "Bitte prüfen" photo is a decision: the mark goes, the choice is stored."""
    from PIL import Image, ImageFilter

    album = tmp_path / "Album"
    for k in range(24):
        make_jpeg(album / f"{k:02d}.jpg", seed=k, taken=f"2025:08:{k + 1:02d} 11:00:00")
    with Image.open(album / "07.jpg") as img:
        exif = img.getexif()
        img.filter(ImageFilter.GaussianBlur(9)).save(album / "07.jpg", exif=exif, quality=90)
    album_id = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()[
        "album_id"
    ]
    client.post(f"/api/album/{album_id}/analyze", headers=POST)
    view = _wait_done(client, album_id)
    blurred = next(p["id"] for p in view["photos"] if p["name"] == "07.jpg")
    before = {d["id"]: d for d in view["review"]["decisions"]}[blurred]
    assert before["keep"] and before["review"] and before["auto_review"]

    url = f"/api/album/{album_id}/decision"
    diff = client.post(url, json={"photo_id": blurred, "manual": "keep"}, headers=POST).json()
    (after,) = diff["changed"]
    assert after["keep"] and after["manual"] == "keep" and not after["review"]
    assert after["auto_review"] and after["reason"] is None
    assert diff["stats"]["review"] == view["review"]["stats"]["review"] - 1
    back = client.post(url, json={"photo_id": blurred, "manual": None}, headers=POST).json()
    assert back["changed"][0]["review"] is True

    seen = f"/api/album/{album_id}/seen"
    assert client.post(seen, json={"items": [[blurred, 4, True]]}, headers=POST).json()["ok"]
    assert client.post(seen, json={"items": [[blurred, 16, True]]}, headers=POST).status_code == 422


def test_design_choice_is_kept_by_the_program(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/prefs").json() == {}
    assert client.post("/api/prefs", json={"design": "nova"}, headers=POST).status_code == 200
    assert client.post("/api/prefs", json={"design": "pink"}, headers=POST).status_code == 422
    fresh = _client()  # next start: new port, new session
    fresh.get(f"/?t={TOKEN}")
    assert fresh.get("/api/prefs").json() == {"design": "nova"}


def test_confirmed_stacks_are_kept_per_album(client: TestClient, tmp_path: Path) -> None:
    album = tmp_path / "Album"
    for k in range(2):
        make_jpeg(album / f"{k}.jpg", seed=k)
    album_id = client.post("/api/album/open", json={"path": str(album)}, headers=POST).json()[
        "album_id"
    ]
    client.post(f"/api/album/{album_id}/analyze", headers=POST)
    _wait_done(client, album_id)
    url = f"/api/album/{album_id}/group-done"
    for sig in ("2-abc", "3-xyz"):
        assert client.post(url, json={"sig": sig, "done": True}, headers=POST).json()["ok"]
    client.post(url, json={"sig": "2-abc", "done": False}, headers=POST)
    assert client.post(url, json={"sig": "a'; DROP", "done": True}, headers=POST).status_code == 422
    client.post("/api/album/open", json={"path": str(album)}, headers=POST)
    assert client.get(f"/api/album/{album_id}/view").json()["review"]["done"] == ["3-xyz"]
