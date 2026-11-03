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
