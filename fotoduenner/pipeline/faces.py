"""Faces (03 §3.5, D17): YuNet detection, face sharpness, eyes open via Face Mesh + Blendshapes.

Coordinates in the stored records are relative to the displayed image (0..1), so they stay
valid for every preview size and for the full-resolution second pass (T6).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from fotoduenner.config import Config
from fotoduenner.pipeline.models import Models
from fotoduenner.pipeline.quality import laplacian_var, luma, resize_long_edge

MERGE_IOU = 0.3  # boxes from both scales overlapping more than this are the same face
FACE_CROP = 160  # face sharpness is measured on a 160x160 crop
FACE_EXPAND = 1.2  # box enlarged by 20 % before cropping
MESH_SIZE = 256  # Face Mesh V2 input
MESH_ROI_SCALE = 1.5
BLINK_LEFT, BLINK_RIGHT = 9, 10  # eyeBlinkLeft / eyeBlinkRight in the 52 blendshapes
EYE_RIGHT_IDX, EYE_LEFT_IDX = 33, 263  # Face Mesh points that define the eye line
# MediaPipe kLandmarksSubsetIdxs (face_blendshapes_graph.cc): 146 points fed to Blendshape V2.
BLENDSHAPE_SUBSET = np.array(
    [0, 1, 4, 5, 6, 7, 8, 10, 13, 14, 17, 21, 33, 37, 39, 40, 46, 52, 53, 54, 55, 58, 61, 63,
     65, 66, 67, 70, 78, 80, 81, 82, 84, 87, 88, 91, 93, 95, 103, 105, 107, 109, 127, 132, 133,
     136, 144, 145, 146, 148, 149, 150, 152, 153, 154, 155, 157, 158, 159, 160, 161, 162, 163,
     168, 172, 173, 176, 178, 181, 185, 191, 195, 197, 234, 246, 249, 251, 263, 267, 269, 270,
     276, 282, 283, 284, 285, 288, 291, 293, 295, 296, 297, 300, 308, 310, 311, 312, 314, 317,
     318, 321, 323, 324, 332, 334, 336, 338, 356, 361, 362, 365, 373, 374, 375, 377, 378, 379,
     380, 381, 382, 384, 385, 386, 387, 388, 389, 390, 397, 398, 400, 402, 405, 409, 415, 454,
     466, 468, 469, 470, 471, 472, 473, 474, 475, 476, 477],
    dtype=np.intp,
)  # fmt: skip


@dataclass(slots=True)
class Face:
    box: tuple[float, float, float, float]  # x, y, w, h relative to the image
    size_rel: float  # box width / image width
    conf: float
    landmarks: list[tuple[float, float]]  # YuNet: right eye, left eye, nose, mouth r, mouth l
    sharpness: float | None = None  # Laplacian variance on the 160x160 crop of img_1600
    sharpness_native: float | None = None  # second pass on the full-resolution original (T6)
    eyes_open: float | None = None  # 1 - max(blink); None = face too small or unsure
    blink: tuple[float, float] | None = None
    presence: float | None = None

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("size_rel", "conf", "sharpness", "sharpness_native", "eyes_open", "presence"):
            if data[key] is not None:
                data[key] = round(float(data[key]), 4)
        data["box"] = [round(float(v), 5) for v in self.box]
        data["landmarks"] = [[round(float(x), 5), round(float(y), 5)] for x, y in self.landmarks]
        if self.blink is not None:
            data["blink"] = [round(float(v), 4) for v in self.blink]
        return data

    @classmethod
    def from_json(cls, data: dict) -> Face:
        return cls(
            box=tuple(data["box"]),  # type: ignore[arg-type]
            size_rel=data["size_rel"],
            conf=data["conf"],
            landmarks=[tuple(p) for p in data["landmarks"]],  # type: ignore[misc]
            sharpness=data.get("sharpness"),
            sharpness_native=data.get("sharpness_native"),
            eyes_open=data.get("eyes_open"),
            blink=tuple(data["blink"]) if data.get("blink") else None,  # type: ignore[arg-type]
            presence=data.get("presence"),
        )


def _detect_at(models: Models, rgb: np.ndarray, edge: int, cfg: Config) -> list[Face]:
    small = resize_long_edge(rgb, edge)
    height, width = small.shape[:2]
    models.yunet.setInputSize((width, height))
    _, rows = models.yunet.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    faces: list[Face] = []
    for row in rows if rows is not None else []:
        x, y, w, h = (float(v) for v in row[:4])
        size_rel = w / width
        if size_rel < cfg.face_min_size_rel:
            continue
        points = row[4:14].reshape(5, 2)
        faces.append(
            Face(
                box=(x / width, y / height, w / width, h / height),
                size_rel=size_rel,
                conf=float(row[14]),
                landmarks=[(float(px) / width, float(py) / height) for px, py in points],
            )
        )
    return faces


def _iou(a: Face, b: Face) -> float:
    ax, ay, aw, ah = a.box
    bx, by, bw, bh = b.box
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def detect(models: Models, rgb: np.ndarray, cfg: Config) -> list[Face]:
    """YuNet at several input sizes (A23), merged; tiny faces (crowds far away) are dropped."""
    found = [f for edge in cfg.face_detect_edges for f in _detect_at(models, rgb, edge, cfg)]
    faces: list[Face] = []
    for face in sorted(found, key=lambda f: f.conf, reverse=True):
        if all(_iou(face, kept) < MERGE_IOU for kept in faces):
            faces.append(face)
    faces.sort(key=lambda f: f.size_rel, reverse=True)
    return faces


def _crop_box(face: Face, width: int, height: int) -> tuple[int, int, int, int]:
    """Expanded face box in pixels, clipped to the image: (x0, y0, x1, y1)."""
    x, y, w, h = face.box
    cx, cy = (x + w / 2) * width, (y + h / 2) * height
    half_w, half_h = w * width * FACE_EXPAND / 2, h * height * FACE_EXPAND / 2
    x0, y0 = max(0, int(cx - half_w)), max(0, int(cy - half_h))
    x1, y1 = min(width, math.ceil(cx + half_w)), min(height, math.ceil(cy + half_h))
    return x0, y0, x1, y1


def face_sharpness(gray: np.ndarray, face: Face, resize_to: int | None = FACE_CROP) -> float | None:
    """Laplacian variance of the expanded face box; ``resize_to=None`` keeps native pixels."""
    height, width = gray.shape
    x0, y0, x1, y1 = _crop_box(face, width, height)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = gray[y0:y1, x0:x1]
    if resize_to is not None:
        crop = cv2.resize(crop, (resize_to, resize_to), interpolation=cv2.INTER_AREA)
    return laplacian_var(crop)


def _roi_affine(cx: float, cy: float, side: float, angle: float) -> np.ndarray:
    """Affine map of the rotated square ROI (centre, side, angle in rad) onto MESH_SIZE²."""
    cos, sin = math.cos(angle), math.sin(angle)
    matrix = np.array([[cos, sin, 0.0], [-sin, cos, 0.0]]) * (MESH_SIZE / side)
    matrix[:, 2] = np.array([MESH_SIZE / 2, MESH_SIZE / 2]) - matrix[:, :2] @ np.array([cx, cy])
    return matrix


def _mesh(models: Models, rgb: np.ndarray, roi: tuple[float, float, float, float]):
    """Run Face Mesh V2 on one ROI; returns (478 x 2 points in image pixels, presence 0..1)."""
    matrix = _roi_affine(*roi)
    crop = cv2.warpAffine(
        rgb, matrix, (MESH_SIZE, MESH_SIZE), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
    )
    tensor = crop.astype(np.float32)[None] / 255.0  # NHWC, RGB 0..1
    outputs = models.landmarks.run(None, {models.landmarks.get_inputs()[0].name: tensor})
    points = next(o for o in outputs if o.size == 478 * 3).reshape(478, 3)[:, :2]
    logit = float(next(o for o in outputs if o.size == 1).ravel()[0])
    inverse = cv2.invertAffineTransform(matrix)
    return points @ inverse[:, :2].T + inverse[:, 2], 1.0 / (1.0 + math.exp(-logit))


def eyes(models: Models, rgb: np.ndarray, face: Face, cfg: Config) -> None:
    """Fill eyes_open/blink/presence (D17). Two passes: ROI from YuNet, then from the mesh."""
    height, width = rgb.shape[:2]
    x, y, w, h = face.box
    (rx, ry), (lx, ly) = face.landmarks[0], face.landmarks[1]
    angle = math.atan2((ly - ry) * height, (lx - rx) * width)
    side = max(w * width, h * height) * MESH_ROI_SCALE
    points, presence = _mesh(models, rgb, ((x + w / 2) * width, (y + h / 2) * height, side, angle))
    # second pass with a ROI from the landmarks (MediaPipe tracking: 33 -> 263, bbox x 1.5)
    eye_vec = points[EYE_LEFT_IDX] - points[EYE_RIGHT_IDX]
    angle = math.atan2(float(eye_vec[1]), float(eye_vec[0]))
    centre = (points.min(0) + points.max(0)) / 2
    cos, sin = math.cos(-angle), math.sin(-angle)
    rotated = (points - centre) @ np.array([[cos, -sin], [sin, cos]]).T
    side = float((rotated.max(0) - rotated.min(0)).max()) * MESH_ROI_SCALE
    points, presence = _mesh(models, rgb, (float(centre[0]), float(centre[1]), side, angle))
    subset = points[BLENDSHAPE_SUBSET].astype(np.float32)[None]
    shapes = models.blendshapes.run(None, {models.blendshapes.get_inputs()[0].name: subset})[0]
    shapes = shapes.ravel()
    blink = (float(shapes[BLINK_LEFT]), float(shapes[BLINK_RIGHT]))
    face.presence = presence
    face.blink = blink
    face.eyes_open = None if presence < cfg.eyes_min_presence else 1.0 - max(blink)


def analyze_faces(models: Models, rgb: np.ndarray, cfg: Config) -> list[Face]:
    """Detection on img_640, sharpness on the working image, eyes for large enough faces."""
    faces = detect(models, rgb, cfg)
    if not faces:
        return faces
    gray = luma(rgb)
    for face in faces:
        face.sharpness = face_sharpness(gray, face)
        if face.size_rel >= cfg.eyes_min_size_rel:
            eyes(models, rgb, face, cfg)
    return faces


def native_sharpness(gray_full: np.ndarray, faces: list[Face]) -> None:
    """Second pass (T6): face sharpness on native pixels of the full-resolution original."""
    for face in faces:
        face.sharpness_native = face_sharpness(gray_full, face, resize_to=None)
