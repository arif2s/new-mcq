"""
Geometric and bounding-box utility functions (`parsers/docling/geometry.py`).

Provides fast rectangular intersection, point containment, area computation,
and vector ruling barrier helpers.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple


def rect_area(rect: Sequence[float]) -> float:
    """Computes the 2D area of a rectangular bounding box `(x0, y0, x1, y1)`."""
    w = max(0.0, float(rect[2]) - float(rect[0]))
    h = max(0.0, float(rect[3]) - float(rect[1]))
    return w * h


def rects_overlap(r1: Sequence[float], r2: Sequence[float]) -> bool:
    """Returns True if rectangular boxes `r1` and `r2` overlap geometrically."""
    return not (
        float(r1[2]) <= float(r2[0])
        or float(r1[0]) >= float(r2[2])
        or float(r1[3]) <= float(r2[1])
        or float(r1[1]) >= float(r2[3])
    )


def point_in_rect(x: float, y: float, rect: Sequence[float]) -> bool:
    """Returns True if 2D coordinate `(x, y)` lies within bounding box `rect`."""
    return (
        float(rect[0]) <= float(x) <= float(rect[2])
        and float(rect[1]) <= float(y) <= float(rect[3])
    )


def rgb_distance(c1: int, c2: int) -> float:
    """Calculates Euclidean color distance between two packed 24-bit RGB integers."""
    if c1 == c2:
        return 0.0
    r1, g1, b1 = (c1 >> 16) & 255, (c1 >> 8) & 255, c1 & 255
    r2, g2, b2 = (c2 >> 16) & 255, (c2 >> 8) & 255, c2 & 255
    return math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)


# Backwards compatibility aliases with leading underscores
_rect_area = rect_area
_rects_overlap = rects_overlap
_point_in_rect = point_in_rect


# --- Phase 3 Vectorized NumPy Bounding-Box Geometry ---
from typing import Sequence as _Sequence
import numpy as _np


def compute_iou_matrix(boxes_a: _Sequence[_Sequence[float]], boxes_b: _Sequence[_Sequence[float]]) -> _np.ndarray:
    if not len(boxes_a) or not len(boxes_b):
        return _np.zeros((len(boxes_a), len(boxes_b)), dtype=_np.float32)
    a = _np.asarray(boxes_a, dtype=_np.float32)[:, :4]
    b = _np.asarray(boxes_b, dtype=_np.float32)[:, :4]
    x0 = _np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = _np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = _np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = _np.minimum(a[:, None, 3], b[None, :, 3])
    inter = _np.maximum(0.0, x1 - x0) * _np.maximum(0.0, y1 - y0)
    area_a = _np.maximum(0.0, a[:, 2] - a[:, 0]) * _np.maximum(0.0, a[:, 3] - a[:, 1])
    area_b = _np.maximum(0.0, b[:, 2] - b[:, 0]) * _np.maximum(0.0, b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return _np.where(union > 1e-6, inter / union, 0.0)


def bbox_iou(box_a: _Sequence[float], box_b: _Sequence[float]) -> float:
    x0, y0 = max(box_a[0], box_b[0]), max(box_a[1], box_b[1])
    x1, y1 = min(box_a[2], box_b[2]), min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - inter
    return float(inter / union) if union > 1e-6 else 0.0
