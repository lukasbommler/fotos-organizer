"""Grouping and selection (03 §5–§9) on synthetic features – acceptance criteria of 05 M4."""

from __future__ import annotations

import time
from dataclasses import replace

import numpy as np
import pytest
from selection_helpers import DAY, T0, Album, P, each, face, flip_bits, jitter, scene

from fotoduenner.config import DEFAULT
from fotoduenner.grouping.cluster import exact_duplicates
from fotoduenner.grouping.sequences import is_bracket, phase_shift
from fotoduenner.selection.engine import Engine
from fotoduenner.selection.modes import Settings
from fotoduenner.selection.reasons import Reason, text
from fotoduenner.selection.scoring import percentile_rank
from fotoduenner.selection.select import mmr

CLEAN = Settings(mode="clean")
QUOTA = Settings(mode="quota", keep_target=0.5)


def kept(decisions, idx):
    return [bool(decisions.keep[i]) for i in idx]


def unit_of(engine: Engine, i: int, level: int = 2):
    layout = engine.layout(level)
    return layout.groups[int(layout.group_of[i])]


# --- building blocks ---------------------------------------------------------------------


def test_percentile_rank_ties_nan_single():
    r = percentile_rank(np.array([3.0, 1.0, np.nan, 3.0, 2.0]))
    assert r[1] == 0.0 and r[4] == pytest.approx(1 / 3) and np.isnan(r[2])
    assert r[0] == r[3] == pytest.approx(5 / 6)
    assert percentile_rank(np.array([7.0]))[0] == 0.5


def test_exact_duplicates_first_by_path_stays():
    dups = exact_duplicates([b"a", b"b", b"a", b"a"], ["z/IMG.jpg", "x.jpg", "A/img.jpg", "b.jpg"])
    assert dups == {0: 2, 3: 2}


def test_mmr_prefers_different_photo_over_near_copy():
    rng = np.random.default_rng(3)
    a, b = scene(1), scene(2)
    emb = np.stack([jitter(a, 0.05, rng), jitter(a, 0.05, rng), jitter(b, 0.05, rng)])
    order, marginal = mmr(np.array([0.9, 0.85, 0.6]), emb, np.zeros(3, dtype=int), DEFAULT)
    assert order.tolist() == [0, 2, 1]
    assert marginal[0] == pytest.approx(0.9)
    assert marginal[1] < 0.01 < marginal[2]


def test_mmr_face_count_diversity():
    rng = np.random.default_rng(4)
    a = scene(5)
    emb = np.stack([jitter(a, 0.2, rng) for _ in range(3)])
    order, _ = mmr(np.array([0.9, 0.8, 0.79]), emb, np.array([1, 1, 3]), DEFAULT)
    assert order[1] == 2  # another constellation of people comes before a similar shot


def test_is_bracket():
    assert is_bracket(np.array([-1.0, 0.0, 1.0]), DEFAULT)
    assert not is_bracket(np.array([0.0, 0.0, 0.0]), DEFAULT)
    assert not is_bracket(np.array([0.0, 0.3, np.nan]), DEFAULT)


def _wide_image(width: int, height: int = 192) -> np.ndarray:
    import cv2

    noise = np.random.default_rng(11).uniform(0, 255, size=(height, width)).astype(np.float32)
    return cv2.GaussianBlur(noise, (0, 0), 3)


def test_phase_shift_measures_panorama_step():
    wide = _wide_image(600)
    a, b = wide[:, :256], wide[:, 100:356]  # b starts 100 px further right: 39 %
    dx, dy, peak = phase_shift(a, b)
    assert abs(abs(dx) - 100 / 256) < 0.02 and abs(dy) < 0.02 and peak > DEFAULT.pano_min_peak
    dx0, _, _ = phase_shift(a, a)
    assert abs(dx0) < 0.01


def test_reason_texts():
    assert text(Reason("duplicate"), "IMG_0412.jpg") == "Exakte Kopie von IMG_0412.jpg."
    assert (
        text(Reason("series", 3, {"aspect": "sharp"}), "2")
        == "Fast identisch mit Bild 2 dieser Serie, aber unschärfer."
    )
    assert (
        text(Reason("series", 3, {"aspect": None}), "2")
        == "Fast identisch mit Bild 2 dieser Serie."
    )
    assert (
        text(Reason("motif", 1, {"kept": 2, "zs": "document_sign"}))
        == "Ähnliches Motiv ist bereits 2× behalten (vermutlich Schild/Speisekarte)."
    )
    assert text(Reason("blur", None, {"rank": 0.04})) == (
        "Deutlich unscharf (schärfer als nur 4 % des Albums)."
    )
    assert text(Reason("overexposed", None, {"white": 0.38})) == (
        "Stark überbelichtet (38 % der Fläche weiß)."
    )
    assert text(Reason("blur_review")) == "Wirkt unscharf – bitte selbst prüfen."
    assert text(Reason("person_quality", None, {"only": True, "problem": "blur"})) == (
        "Einziges Bild dieser Personen hier, aber unscharf – bitte selbst entscheiden."
    )


# --- acceptance criteria (05 M4) ---------------------------------------------------------


def test_ten_near_identical_shots_one_series_sharpest_kept():
    album = Album(seed=2)
    sharp = [120, 150, 90, 300, 110, 140, 100, 130, 95, 125]
    burst = album.series(T0, 10, step=2.0, sharp=each(sharp))
    album.background(30)
    engine = Engine(album.build(), DEFAULT)
    layout = engine.layout(2)
    assert len({int(layout.series_of[i]) for i in burst}) == 1 and layout.series_of[burst[0]] >= 0
    assert not engine.in_sequence[burst].any()  # a burst is no panorama or bracket

    clean = engine.decide(CLEAN)
    assert kept(clean, burst).count(True) == 1 and clean.keep[burst[3]]
    reason = clean.reasons[burst[0]]
    assert reason is not None and reason.code == "series" and reason.ref == burst[3]
    assert reason.params["aspect"] == "sharp"

    quota = engine.decide(QUOTA)
    assert quota.keep[burst[3]]


def test_clean_keeps_distinct_series_members():
    album = Album(seed=3)
    base = scene(77)
    ph = 0x1234_5678_9ABC_DEF0
    shots = [
        album.add(P(t=T0 + k * 2, emb=jitter(base, 0.5 if k == 2 else 0.05, album.rng),
                    phash=flip_bits(ph, 1, album.rng), sharp=200 - k))
        for k in range(4)
    ]  # fmt: skip
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    d = engine.decide(CLEAN)
    assert kept(d, shots) == [True, False, True, False]  # shot 2 brings something of its own


def test_clean_keeps_other_people_constellation():
    album = Album(seed=4)
    burst = album.series(
        T0, 3, faces=each([[face(sharp=300)], [face(sharp=250)], [face(), face(size=0.11)]])
    )
    album.background(20)
    d = Engine(album.build(), DEFAULT).decide(CLEAN)
    assert kept(d, burst) == [True, False, True]


def test_clean_never_thins_motif_groups_but_quota_does():
    album = Album(seed=5)
    group = album.motif(T0, 6)
    album.background(30)
    engine = Engine(album.build(), DEFAULT)
    unit = unit_of(engine, group[0])
    assert unit.kind == "motif" and sorted(unit.members.tolist()) == group
    assert all(kept(engine.decide(CLEAN), group))
    assert not all(kept(engine.decide(replace(QUOTA, keep_target=0.3)), group))


def test_exact_copies_sorted_out_in_both_modes():
    album = Album(seed=6)
    original = album.single(T0, file_hash=b"same", path="a/IMG_1.jpg")
    copy = album.add(replace(album.photos[original], path="b/IMG_1.jpg"))
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    for settings in (CLEAN, QUOTA):
        d = engine.decide(settings)
        assert d.keep[original] and not d.keep[copy]
        assert d.reasons[copy] == Reason("duplicate", original)
    assert engine.layout(2).group_of[copy] == -1


def test_exposure_bracket_kept_completely():
    album = Album(seed=7)
    bracket = album.series(T0, 3, step=1.0, bias=each([-1.0, 0.0, 1.0]),
                           sharp=each([100, 200, 150]), mean=each([60, 120, 245]),
                           clip_high=each([0.0, 0.02, 0.4]))  # fmt: skip
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    assert [s.kind for s in engine.sequences] == ["bracket"]
    for settings in (CLEAN, QUOTA, replace(QUOTA, keep_target=0.3)):
        assert all(kept(engine.decide(settings), bracket))
    assert unit_of(engine, bracket[0]).kind == "bracket"


def test_panorama_sequence_kept_completely():
    wide = _wide_image(256 + 4 * 102)
    crops = [wide[:, k * 102 : k * 102 + 256] for k in range(5)]  # 40 % steps
    album = Album(seed=8)
    base = scene(99)
    pano = [
        album.add(P(t=T0 + k, emb=jitter(base, 0.15, album.rng),
                    phash=int(album.rng.integers(0, 2**63)), sharp=100 + k))
        for k in range(5)
    ]  # fmt: skip
    album.background(20)

    def shift(a: int, b: int):
        return phase_shift(crops[pano.index(a)], crops[pano.index(b)]) if b in pano else None

    engine = Engine(album.build(), DEFAULT, shift_fn=shift)
    assert [(s.kind, list(s.members)) for s in engine.sequences] == [("pano", pano)]
    for settings in (CLEAN, replace(QUOTA, keep_target=0.3)):
        assert all(kept(engine.decide(settings), pano))
    # without the shift measurement the same photos are one ordinary series and get thinned
    plain = Engine(album.build(), DEFAULT)
    assert plain.sequences == [] and not all(kept(plain.decide(CLEAN), pano))


def test_blur_uncalibrated_only_review():
    album = Album(seed=9)
    blurry = album.single(T0, sharp=1.0)
    album.background(40)
    engine = Engine(album.build(), DEFAULT)
    for settings in (CLEAN, QUOTA, replace(QUOTA, keep_target=0.3)):
        d = engine.decide(settings)
        assert d.keep[blurry] and d.review[blurry]
        assert d.reasons[blurry].code == "blur_review"
    calibrated = Engine(album.build(), replace(DEFAULT, blur_abs=10.0))
    d = calibrated.decide(CLEAN)
    assert not d.keep[blurry] and d.reasons[blurry].code == "blur"


def test_people_photo_blur_needs_blurry_faces():
    album = Album(seed=10)
    portrait = album.single(T0, sharp=1.0, faces=[face(size=0.3, sharp=500)])  # bokeh
    album.background(40)
    d = Engine(album.build(), replace(DEFAULT, blur_abs=10.0)).decide(CLEAN)
    assert d.keep[portrait] and not d.review[portrait]


def test_single_photo_kept_at_every_quota():
    album = Album(seed=11)
    single = album.single(T0, sharp=81.0, aes=1.0)  # weak single photo, no people
    for k in range(40):  # 40 groups of 2: their best photos alone exceed a 30 % target
        album.motif(T0 + (k + 1) * DAY, 2)
    engine = Engine(album.build(), DEFAULT)
    assert unit_of(engine, single).kind == "single"
    for p in (0.3, 0.5, 0.9):
        assert engine.decide(replace(QUOTA, keep_target=p)).keep[single]
    d = engine.decide(replace(QUOTA, keep_target=0.3, low_singles=True))
    assert not d.keep[single] and d.reasons[single] == Reason("low_single")


def test_people_photo_not_lost_for_quality():
    album = Album(seed=12)
    landscape = album.motif(T0, 3)
    person = album.add(P(t=T0 + 1200, emb=jitter(album.photos[landscape[0]].emb, 0.2, album.rng),
                         phash=1, mean=250, clip_high=0.5, faces=[face()]))  # fmt: skip
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    assert engine.layout(2).group_of[person] == engine.layout(2).group_of[landscape[0]]
    for settings in (CLEAN, QUOTA, replace(QUOTA, keep_target=0.3)):
        d = engine.decide(settings)
        assert d.keep[person] and d.review[person]
        assert d.reasons[person] == Reason(
            "person_quality", None, {"only": True, "problem": "overexposed"}
        )


def test_people_photo_never_lost_by_quota():
    album = Album(seed=13)
    group = album.motif(
        T0, 6, sharp=each([300, 290, 280, 270, 260, 20]), aes=each([6, 6, 6, 6, 6, 3])
    )
    album.photos[group[5]].faces = [face(sharp=10, eyes=0.2)]  # weak, but the only person
    album.background(10)
    engine = Engine(album.build(), DEFAULT)
    d = engine.decide(replace(QUOTA, keep_target=0.3))
    assert d.keep[group[5]]
    assert not all(kept(d, group))


def test_rating_never_sorted_out():
    album = Album(seed=14)
    burst = album.series(T0, 5, sharp=each([300, 100, 100, 100, 100]), rating=each([0, 1, 0, 0, 2]))
    starred_copy = album.add(replace(album.photos[burst[0]], rating=3, path="zz_copy.jpg",
                                     file_hash=b"x"))  # fmt: skip
    album.photos[burst[0]].file_hash = b"x"
    blurry = album.single(T0 + 1000, sharp=0.5, mean=250, clip_high=0.6, rating=1)
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    for settings in (CLEAN, QUOTA, replace(QUOTA, keep_target=0.3)):
        d = engine.decide(settings)
        assert d.keep[[burst[1], burst[4], starred_copy, blurry]].all()


def test_manual_decisions_survive_settings_changes():
    album = Album(seed=15)
    burst = album.series(T0, 4)
    group = album.motif(T0 + DAY, 5)
    album.background(20)
    engine = Engine(album.build(), DEFAULT)
    auto = engine.decide(CLEAN)
    loser = next(i for i in burst if not auto.keep[i])
    manual = {loser: "keep", group[0]: "reject"}
    variants = (CLEAN, QUOTA, replace(QUOTA, keep_target=0.3), replace(QUOTA, level=0),
                replace(QUOTA, level=4, keep_target=0.9), replace(CLEAN, level=4))  # fmt: skip
    for settings in variants:
        d = engine.decide(settings, manual)
        assert d.keep[loser] and not d.keep[group[0]]
        assert d.reasons[group[0]] == Reason("manual") and d.reasons[loser] is None


def _realistic_album(seed: int = 16, days: int = 6) -> Album:
    album = Album(seed=seed)
    rng = album.rng
    t = T0
    for _ in range(days):
        for _ in range(12):
            kind = rng.random()
            people = rng.random() < 0.4
            faces = [face(sharp=float(rng.uniform(50, 400)), eyes=float(rng.uniform(0, 1)))]
            if kind < 0.35:
                album.series(t, int(rng.integers(2, 8)), faces=faces if people else [])
            elif kind < 0.6:
                album.motif(t, int(rng.integers(2, 6)), step=300.0)
            else:
                album.single(t, faces=faces if people else [])
            t += float(rng.uniform(900, 3600))
        t += DAY / 2
    return album


@pytest.mark.parametrize("settings", [CLEAN, QUOTA, replace(QUOTA, keep_target=0.35)])
def test_t1_manual_click_changes_no_other_photo(settings):
    engine = Engine(_realistic_album().build(), DEFAULT)
    base = engine.decide(settings)
    rng = np.random.default_rng(0)
    for i in rng.choice(engine.n, size=25, replace=False):
        for choice in ("keep", "reject"):
            d = engine.decide(settings, {int(i): choice})
            others = np.ones(engine.n, dtype=bool)
            others[i] = False
            assert (d.keep[others] == base.keep[others]).all()


def test_quota_fifty_percent_on_synthetic_album():
    album = Album(seed=17)
    t = T0
    for _ in range(100):
        album.motif(t, 4, step=300.0)
        t += 4 * 3600
    for _ in range(200):
        album.single(t)
        t += 3600
    engine = Engine(album.build(), DEFAULT)
    assert sum(len(u.members) == 4 for u in engine.layout(2).groups) == 100
    for p in (0.5, 0.6, 0.75):
        d = engine.decide(replace(QUOTA, keep_target=p))
        assert d.keep.mean() == pytest.approx(p, abs=0.02)


def test_mega_group_split_above_thirty_photos():
    album = Album(seed=18)
    a, b = scene(501), scene(502)
    sub = [0.6 * a + 0.4 * b, 0.4 * a + 0.6 * b]  # two framings of one subject, cos 0.92
    sub = [v / np.linalg.norm(v) for v in sub]
    for k in range(40):
        album.add(P(t=T0 + k * 120, emb=jitter(sub[k // 20], 0.15, album.rng),
                    phash=int(album.rng.integers(0, 2**63))))  # fmt: skip
    data = album.build()
    unsplit = Engine(data, replace(DEFAULT, group_max_size=1000)).layout(2)
    assert [len(u.members) for u in unsplit.groups] == [40]
    sizes = sorted(len(u.members) for u in Engine(data, DEFAULT).layout(2).groups)
    assert max(sizes) <= 30 and sum(sizes) == 40


def test_mega_group_split_above_one_day():
    album = Album(seed=19)
    base = scene(600)
    for k in range(6):  # same subject on two days, 3 photos each
        album.add(P(t=T0 + (k // 3) * DAY * 1.5 + k * 60, emb=jitter(base, 0.12, album.rng),
                    phash=int(album.rng.integers(0, 2**63))))  # fmt: skip
    data = album.build()
    unsplit = Engine(data, replace(DEFAULT, group_max_span=1e9)).layout(2)
    assert [len(u.members) for u in unsplit.groups] == [6]
    sizes = sorted(len(u.members) for u in Engine(data, DEFAULT).layout(2).groups)
    assert sizes == [3, 3]


def test_untimed_photos_group_only_when_nearly_equal():
    album = Album(seed=20)
    base = scene(700)
    loose = [album.add(P(t=T0 + k * 600, emb=jitter(base, 0.3, album.rng), phash=k * 9999,
                         timed=False)) for k in range(3)]  # fmt: skip
    album.background(10)
    engine = Engine(album.build(), DEFAULT)
    assert len({int(engine.layout(2).group_of[i]) for i in loose}) == 3


def test_recalculation_under_300_ms_for_2000_photos():
    album = _realistic_album(seed=21, days=60)
    while len(album.photos) < 2000:
        album.single(T0 + 400 * DAY + len(album.photos) * 600)
    engine = Engine(album.build(), DEFAULT)
    engine.layout(2)
    manual = {i: "keep" for i in range(0, 2000, 37)}

    def best(fn) -> float:
        times = []
        for _ in range(3):
            start = time.perf_counter()
            fn()
            times.append(time.perf_counter() - start)
        return min(times)

    assert best(lambda: engine.decide(QUOTA, manual)) < 0.3
    assert best(lambda: engine.decide(CLEAN, manual)) < 0.3

    def new_level() -> None:
        engine._layouts.clear()
        engine.decide(replace(QUOTA, level=3), manual)

    assert best(new_level) < 0.3


def test_empty_and_tiny_albums():
    for count in (0, 1, 2):
        album = Album(seed=30)
        album.background(count)
        engine = Engine(album.build(), DEFAULT)
        for settings in (CLEAN, QUOTA):
            d = engine.decide(settings)
            assert d.keep.all() and d.stats["total"] == count


def test_blurry_series_copy_is_sorted_out_as_copy():
    album = Album(seed=31)
    burst = album.series(T0, 3, sharp=each([300.0, 250.0, 0.5]))
    album.background(40)
    d = Engine(album.build(), DEFAULT).decide(CLEAN)
    assert kept(d, burst) == [True, False, False]
    assert d.reasons[burst[2]].code == "series" and not d.review[burst[2]]
    assert d.reasons[burst[2]].params["aspect"] == "sharp"


def test_quota_reason_mentions_series_or_motif():
    album = Album(seed=32)
    burst = album.series(T0, 4, sharp=each([300.0, 100.0, 100.0, 100.0]))
    group = album.motif(T0 + 3 * DAY, 6)
    album.background(10)
    d = Engine(album.build(), DEFAULT).decide(replace(QUOTA, keep_target=0.3))
    assert {d.reasons[i].code for i in burst if not d.keep[i]} == {"series"}
    motif = [d.reasons[i] for i in group if not d.keep[i]]
    assert motif and all(r.code == "motif" and r.params["kept"] >= 1 for r in motif)
