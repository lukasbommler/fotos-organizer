"""Review model of an opened album: groups, proposals, manual decisions, settings (04 §1.4, §5).

The selection engine is built once from the cache (after every analysis again). Settings and
clicks only re-run the decision step and answer with the photos whose state changed.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

import numpy as np

from fotoduenner.config import Config
from fotoduenner.grouping.sequences import Shift, phase_shift
from fotoduenner.pipeline.thumbs import load_gray, thumb_paths
from fotoduenner.selection.engine import Engine, input_from_rows
from fotoduenner.selection.modes import Decisions, Layout, Settings, hold
from fotoduenner.selection.reasons import Reason, text
from fotoduenner.store.calibration import Shifts, Snapshot
from fotoduenner.store.db import AlbumStore

log = logging.getLogger(__name__)

PANO_EDGE = 256  # img_256 of 03 §2, taken from the small preview
MANUAL = ("keep", "reject")


def _bool(value: str | None, default: bool) -> bool:
    return default if value is None else value == "1"


class Review:
    def __init__(self, store: AlbumStore, cfg: Config) -> None:
        self.store = store
        self.cfg = cfg
        self.lock = threading.Lock()
        self.engine: Engine | None = None
        self.index: dict[int, int] = {}  # photo id -> engine index
        self.others = 0  # photos without analysis (pending, error): always kept
        self.last: dict[int, tuple[Any, ...]] = {}
        self._gray: dict[int, np.ndarray | None] = {}
        self._shifts: Shifts = {}  # measured panorama shifts, part of the calibration record

    def invalidate(self) -> None:
        with self.lock:
            self.engine = None
            self.last = {}

    # --- loading ------------------------------------------------------------------------

    def _gray_of(self, photo_id: int) -> np.ndarray | None:
        if photo_id not in self._gray:
            small, _ = thumb_paths(self.store.thumbs_dir, photo_id)
            self._gray[photo_id] = load_gray(small, PANO_EDGE)
        return self._gray[photo_id]

    def _ensure(self) -> Engine:
        if self.engine is not None:
            return self.engine
        start = time.perf_counter()
        with contextlib.closing(self.store.connect()) as conn:
            rows = self.store.selection_rows(conn)
            present = conn.execute("SELECT COUNT(*) FROM photos WHERE present=1").fetchone()[0]
        inp = input_from_rows(rows, self.cfg)
        ids = inp.ids
        shifts: Shifts = {}

        def shift(a: int, b: int) -> Shift | None:
            ga, gb = self._gray_of(int(ids[a])), self._gray_of(int(ids[b]))
            result = None if ga is None or gb is None else phase_shift(ga, gb)
            shifts[int(ids[a]), int(ids[b])] = result
            return result

        self.engine = Engine(inp, self.cfg, shift_fn=shift)
        self._gray.clear()
        self._shifts = shifts
        self.index = {int(pid): k for k, pid in enumerate(ids)}
        self.others = int(present) - len(ids)
        log.info(
            "selection model for %d photos built in %.0f ms",
            len(ids),
            (time.perf_counter() - start) * 1000,
        )
        return self.engine

    # --- settings and manual decisions ----------------------------------------------------

    def _settings(self) -> Settings:
        with contextlib.closing(self.store.connect()) as conn:
            raw = self.store.get_settings(conn)
        cfg = self.cfg
        lo, hi = cfg.target_keep_range
        mode = raw.get("mode", cfg.default_mode)
        try:
            target = float(raw.get("keep_target", cfg.target_keep_default))
            level = int(raw.get("level", cfg.motif_default_level))
        except ValueError:
            target, level = cfg.target_keep_default, cfg.motif_default_level
        return Settings(
            mode="quota" if mode == "quota" else "clean",
            keep_target=min(hi, max(lo, target)),
            level=min(len(cfg.motif_thresholds) - 1, max(0, level)),
            low_singles=_bool(raw.get("low_singles"), False),
        )

    def _flags(self) -> dict[str, Any]:
        """Album status after "Übernehmen" (T2), kept in the settings table."""
        with contextlib.closing(self.store.connect()) as conn:
            raw = self.store.get_settings(conn)
        count = raw.get("count_before", "")
        return {
            "applied": raw.get("applied") == "1",
            "hold": raw.get("hold") == "1",
            "count_before": int(count) if count.isdigit() else None,
        }

    def _manual(self) -> tuple[dict[int, str], dict[int, str]]:
        """Manual decisions by photo id and by engine index."""
        with contextlib.closing(self.store.connect()) as conn:
            by_id = self.store.manual_decisions(conn)
        by_index = {self.index[pid]: m for pid, m in by_id.items() if pid in self.index}
        return by_id, by_index

    # --- payloads -------------------------------------------------------------------------

    def _ref_label(self, layout: Layout, reason: Reason) -> str | None:
        engine = self.engine
        assert engine is not None
        if reason.ref is None:
            return None
        if reason.code == "duplicate":
            return engine.inp.rel_paths[reason.ref].rsplit("/", 1)[-1]  # POSIX rel path
        s = int(layout.series_of[reason.ref])
        if s >= 0:
            members = layout.series[s].members.tolist()
            return str(members.index(reason.ref) + 1)
        return None

    def _photo_state(
        self, layout: Layout, d: Decisions, manual: dict[int, str], i: int
    ) -> tuple[Any, ...]:
        engine = self.engine
        assert engine is not None
        reason = d.reasons[i]
        ref = None if reason is None or reason.ref is None else int(engine.inp.ids[reason.ref])
        return (
            bool(d.keep[i]),
            bool(d.auto_keep[i]),
            manual.get(i),
            bool(d.review[i]),
            bool(d.auto_review[i]),
            text(reason, self._ref_label(layout, reason)) if reason else None,
            reason.code if reason else None,
            ref,
        )

    @staticmethod
    def _state_json(pid: int, st: tuple[Any, ...]) -> dict[str, Any]:
        keep, auto, manual, review, auto_review, reason, code, ref = st
        return {
            "id": pid,
            "keep": keep,
            "auto": auto,
            "manual": manual,
            "review": review,
            "auto_review": auto_review,
            "reason": reason,
            "code": code,
            "ref": ref,
        }

    def _stats(self, d: Decisions, settings: Settings) -> dict[str, Any]:
        stats: dict[str, Any] = dict(d.stats)
        stats.update(self._flags())
        stats["total"] += self.others
        stats["keep"] += self.others
        stats["unanalysed"] = self.others
        stats["keep_floor"] = d.keep_floor
        stats["target"] = d.target
        stats["mode"] = settings.mode
        stats["keep_target"] = settings.keep_target
        stats["level"] = settings.level
        stats["low_singles"] = settings.low_singles
        return stats

    def _structure(self, layout: Layout) -> dict[str, Any]:
        engine = self.engine
        assert engine is not None
        ids = engine.inp.ids
        scores = engine.scores
        groups: list[dict[str, Any]] = []
        singles: list[int] = []
        for unit in sorted(layout.groups, key=lambda u: engine.capture_pos[u.members[0]]):
            members = unit.members.tolist()
            if len(members) == 1:
                singles.append(int(ids[members[0]]))
                continue
            parts: list[list[int]] = []
            seen: set[int] = set()
            for i in members:  # series and sequences inside the group, in capture order
                if i in seen:
                    continue
                s = int(layout.series_of[i])
                if s >= 0:
                    part = layout.series[s].members.tolist()
                else:
                    seq = next((q.members for q in engine.sequences if i in q.members), (i,))
                    part = list(seq)
                seen.update(part)
                parts.append([int(ids[k]) for k in part])
            groups.append(
                {
                    "n": len(groups) + 1,
                    "kind": unit.kind,
                    "ids": [int(ids[k]) for k in members],
                    "parts": parts,
                    "ranked": [int(ids[k]) for k in unit.order.tolist()],
                    "people": bool(scores.people[members].any()),
                    "start": float(engine.inp.times[members[0]]),
                    "end": float(engine.inp.times[members[-1]]),
                }
            )
        photos = {
            int(ids[i]): {
                "people": bool(scores.people[i]),
                "faces": int(scores.face_count_main[i]),
                "eyes_closed": bool(scores.eyes_closed[i]),
                "protected": bool(engine.inp.ratings[i] >= 1),
                "value": round(float(scores.value[i]), 3),
                "sharp": round(float(scores.q_sharp[i]), 3),
                "exposure": round(float(scores.q_exp[i]), 3),
                "eyes_known": any(
                    engine.inp.facts.faces[i][k].get("eyes_open") is not None
                    for k in scores.main_faces[i]
                ),
                "sequence": bool(engine.in_sequence[i]),
            }
            for i in range(engine.n)
        }
        return {
            "groups": groups,
            "singles": singles,
            "duplicates": [
                {"id": int(ids[dup]), "of": int(ids[orig])}
                for dup, orig in sorted(engine.duplicates.items())
            ],
            "facts": photos,
        }

    def _compute(self) -> tuple[Engine, Layout, Settings, Decisions, dict[int, str]]:
        engine = self._ensure()
        settings = self._settings()
        _, manual = self._manual()
        layout = engine.layout(settings.level)
        if self._flags()["hold"]:
            return engine, layout, settings, hold(engine.n, manual), manual
        return engine, layout, settings, engine.decide(settings, manual), manual

    def full(self) -> dict[str, Any]:
        with self.lock:
            engine, layout, settings, d, manual = self._compute()
            ids = engine.inp.ids
            self.last = {
                int(ids[i]): self._photo_state(layout, d, manual, i) for i in range(engine.n)
            }
            return {
                "decisions": [self._state_json(pid, st) for pid, st in self.last.items()],
                "stats": self._stats(d, settings),
                "done": self.done(),
                **self._structure(layout),
            }

    def _diff(self, with_structure: bool) -> dict[str, Any]:
        start = time.perf_counter()
        engine, layout, settings, d, manual = self._compute()
        computed = time.perf_counter()
        ids = engine.inp.ids
        changed = []
        current: dict[int, tuple[Any, ...]] = {}
        for i in range(engine.n):
            pid = int(ids[i])
            st = self._photo_state(layout, d, manual, i)
            current[pid] = st
            if self.last.get(pid) != st:
                changed.append(self._state_json(pid, st))
        self.last = current
        out: dict[str, Any] = {"changed": changed, "stats": self._stats(d, settings)}
        if with_structure:
            out.update(self._structure(layout))
        log.debug(
            "recalculated in %.0f ms (+%.0f ms diff), %d changed",
            (computed - start) * 1000,
            (time.perf_counter() - computed) * 1000,
            len(changed),
        )
        return out

    def rejected(self) -> tuple[list[int], dict[str, Any]]:
        """Photo ids "Übernehmen" would move now, with the statistics of that state."""
        with self.lock:
            engine, _, settings, d, _ = self._compute()
            ids = [int(engine.inp.ids[i]) for i in np.flatnonzero(~d.keep)]
            return ids, self._stats(d, settings)

    def done(self) -> list[str]:
        with contextlib.closing(self.store.connect()) as conn:
            return self.store.done_groups(conn)

    def set_group_done(self, sig: str, done: bool) -> None:
        with contextlib.closing(self.store.connect()) as conn:
            self.store.set_group_done(conn, sig, done)

    def add_seen(self, items: list[tuple[int, int, bool]]) -> None:
        """Photos the user looked at (D20); without a proposal there is nothing to agree with."""
        if self._flags()["hold"]:
            return
        with contextlib.closing(self.store.connect()) as conn:
            self.store.add_seen(conn, items)

    def snapshot(self) -> Snapshot | None:
        """Proposal and manual decisions for the calibration record (D20).

        None while the album is held after an earlier "Übernehmen": there is no proposal then.
        """
        with self.lock:
            if self._flags()["hold"]:
                return None
            engine = self._ensure()
            settings = self._settings()
            _, manual = self._manual()
            auto = engine.decide(settings, {})
            ids = engine.inp.ids
            with contextlib.closing(self.store.connect()) as conn:
                rows = [r for r in self.store.selection_rows(conn) if r["id"] in self.index]
                seen = self.store.seen(conn)
            decisions: dict[int, tuple[Any, ...]] = {}
            for i in range(engine.n):
                reason = auto.reasons[i]
                ref = None if reason is None or reason.ref is None else int(ids[reason.ref])
                how, shown = seen.get(int(ids[i]), (0, 0))
                decisions[int(ids[i])] = (
                    int(auto.auto_keep[i]),
                    int(auto.review[i]),
                    reason.code if reason else None,
                    ref,
                    manual.get(i),
                    how if shown == int(auto.auto_keep[i]) else 0,
                )
            return Snapshot(settings, rows, decisions, dict(self._shifts))

    def rethin(self) -> dict[str, Any]:
        """ "Erneut ausdünnen" (T2): proposals again, for the photos left in the album."""
        with contextlib.closing(self.store.connect()) as conn:
            self.store.put_settings(conn, {"hold": "0"})
        return self.full()

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            before = self._settings()
            values: dict[str, str] = {}
            if changes.get("mode") in ("clean", "quota"):
                values["mode"] = changes["mode"]
            if changes.get("keep_target") is not None:
                values["keep_target"] = repr(float(changes["keep_target"]))
            if changes.get("level") is not None:
                values["level"] = str(int(changes["level"]))
            if changes.get("low_singles") is not None:
                values["low_singles"] = "1" if changes["low_singles"] else "0"
            with contextlib.closing(self.store.connect()) as conn:
                self.store.put_settings(conn, values)
            after = self._settings()
            return self._diff(with_structure=after.level != before.level)

    def set_manual(self, photo_ids: list[int], manual: str | None) -> dict[str, Any]:
        with self.lock:
            self._ensure()
            missing = [pid for pid in photo_ids if pid not in self.index]
            if missing:
                raise KeyError(missing[0])
            with contextlib.closing(self.store.connect()) as conn:
                for pid in photo_ids:
                    self.store.set_manual(conn, pid, manual if manual in MANUAL else None)
            return self._diff(with_structure=False)
