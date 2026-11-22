"""Short German reasons for every sorted-out and every "Bitte prüfen" photo (03 §9)."""

from __future__ import annotations

from dataclasses import dataclass, field

from fotoduenner.config import Config

# Partial values compared to name the dominant difference in a series reason.
ASPECT_TEXT = {
    "eyes": "Augen geschlossen",
    "sharp": "unschärfer",
    "exposure": "schlechter belichtet",
}
PROBLEM_TEXT = {
    "blur": "unscharf",
    "blur_review": "unscharf",
    "overexposed": "stark überbelichtet",
    "underexposed": "stark unterbelichtet",
    "misfire": "kaum Bildinhalt",
}
ZS_HINT = {"document_sign": "Schild/Speisekarte", "screenshot": "Screenshot"}


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    ref: int | None = None  # photo index the reason refers to (highlighted in the UI)
    params: dict[str, object] = field(default_factory=dict)


def dominant_aspect(
    cand: dict[str, float], ref: dict[str, float], people: bool, eyes_closed: bool, cfg: Config
) -> str | None:
    """Biggest weighted shortfall of ``cand`` against ``ref`` (sharp/exposure/face).

    The aesthetic score is too coarse to tell near-identical shots apart (03 §3.7): it is never
    named as the reason, the sentence then just says "Fast identisch mit Bild 2 dieser Serie."
    """
    if people:
        w_sharp, w_exp, _, w_face = cfg.value_weights_people
    else:
        (w_sharp, w_exp, _), w_face = cfg.value_weights_other, 0.0
    diffs = {
        "sharp": w_sharp * (ref["sharp"] - cand["sharp"]),
        "exposure": w_exp * (ref["exposure"] - cand["exposure"]),
        "eyes": w_face * (ref["face"] - cand["face"]) if eyes_closed else 0.0,
    }
    aspect, diff = max(diffs.items(), key=lambda kv: kv[1])
    return aspect if diff >= cfg.reason_min_diff else None


def text(reason: Reason, ref_label: str | None = None) -> str:
    """German sentence. ``ref_label`` names the reference: position ("2") or file name."""
    p = reason.params
    match reason.code:
        case "duplicate":
            return f"Exakte Kopie von {ref_label}."
        case "series":
            aspect = p.get("aspect")
            base = f"Fast identisch mit Bild {ref_label} dieser Serie"
            return f"{base}, aber {ASPECT_TEXT[str(aspect)]}." if aspect else f"{base}."
        case "motif":
            hint = ZS_HINT.get(str(p.get("zs") or ""))
            tail = f" (vermutlich {hint})" if hint else ""
            return f"Ähnliches Motiv ist bereits {p['kept']}× behalten{tail}."
        case "low_single":
            return "Einzelbild mit geringem Bildwert."
        case "rating_rejected":
            return "In Lightroom als „abgelehnt“ markiert."
        case "misfire":
            return "Vermutlich versehentlich ausgelöst (kaum Bildinhalt)."
        case "overexposed":
            return f"Stark überbelichtet ({_pct(p.get('white', 0.0))} % der Fläche weiß)."
        case "underexposed":
            return "Stark unterbelichtet (fast schwarz)."
        case "blur":
            return f"Deutlich unscharf (schärfer als nur {_pct(p.get('rank', 0.0))} % des Albums)."
        case "blur_review":
            return "Wirkt unscharf – bitte selbst prüfen."
        case "person_quality":
            who = "Einziges" if p.get("only") else "Bestes"
            problem = PROBLEM_TEXT.get(str(p.get("problem")), "technisch schwach")
            return f"{who} Bild dieser Personen hier, aber {problem} – bitte selbst entscheiden."
        case "manual":
            return "Von Ihnen aussortiert."
    return ""


def _pct(fraction: object) -> int:
    return round(float(fraction) * 100)  # type: ignore[arg-type]
