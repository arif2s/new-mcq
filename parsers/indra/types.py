from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Optional, Sequence, TypeVar

K = TypeVar("K")
V = TypeVar("V")

ElementRole = Literal[
    "heading",
    "paragraph",
    "list_item",
    "table",
    "figure_caption",
    "callout",
    "furniture",
]
BBox = tuple[float, float, float, float]
RGB = tuple[int, int, int]


class ThreadSafeLRUCache(Generic[K, V]):
    """Thread-safe bounded LRU cache preventing concurrent eviction errors."""

    def __init__(self, maxsize: int = 128) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0, got {maxsize}")
        self.maxsize = int(maxsize)
        self._store: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            if key not in self._store:
                return default
            self._store.move_to_end(key)
            val = self._store[key]
            return copy.deepcopy(val) if isinstance(val, (dict, list)) else val

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def pop(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            return self._store.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._store

    def __getitem__(self, key: K) -> V:
        with self._lock:
            val = self.get(key)
            if val is None and key not in self._store:
                raise KeyError(key)
            return val

    def __setitem__(self, key: K, value: V) -> None:
        self.put(key, value)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


@dataclass
class TableGrid:
    bbox_norm: BBox
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""
    is_dense_markdown: bool = False
    headers: list[str] = field(default_factory=list)


@dataclass
class PageElement:
    element_id: int
    text: str
    bbox_norm: BBox
    size_pt: float
    rel_size: float
    fg_rgb: RGB = (0, 0, 0)
    bg_rgb: RGB = (255, 255, 255)
    in_banner: bool = False
    is_all_caps: bool = False
    is_bold: bool = False
    is_italic: bool = False
    is_accent_color: bool = False
    prefix_family: Optional[str] = None
    prefix_index: Optional[int] = None
    column_index: int = 0
    in_callout: bool = False
    in_table: bool = False
    in_figure: bool = False
    is_suppressed_furniture: bool = False
    role: ElementRole = "paragraph"
    heading_level: int = 0
    heading_confidence: float = 0.0
    heading_path: str = ""
    factor_scores: dict[str, float] = field(default_factory=dict)
    formatted_text: str = ""
    font_family: str = "serif"
    first_bold: bool = False
    fg_hue: float = 0.0
    bg_hue: float = 0.0
    thickness: float = 1.0

    @property
    def center_y(self) -> float:
        return 0.5 * (self.bbox_norm[1] + self.bbox_norm[3])

    @property
    def height_norm(self) -> float:
        return max(0.001, self.bbox_norm[3] - self.bbox_norm[1])

    @property
    def width_norm(self) -> float:
        return max(0.001, self.bbox_norm[2] - self.bbox_norm[0])


@dataclass
class PageElements:
    page_number: int
    width_pt: float
    height_pt: float
    profile_id: str
    elements: list[PageElement] = field(default_factory=list)
    tables: list[TableGrid] = field(default_factory=list)
    figure_zones: list[BBox] = field(default_factory=list)
    callout_zones: list[BBox] = field(default_factory=list)
    underlines: list[tuple[BBox, RGB]] = field(default_factory=list)
    banners: list[tuple[BBox, RGB]] = field(default_factory=list)
    column_gutters: list[float] = field(default_factory=list)
    rotation: float = 0.0
    skew: float = 0.0
    vertical: bool = False
    source: str = "text"


@dataclass(frozen=True)
class HeadingPrototype:
    level: int
    rel_size: float
    fg_rgb: RGB
    bg_rgb: RGB
    in_banner: bool
    is_all_caps: bool
    is_bold: bool
    is_accent_color: bool
    prefix_family: Optional[str]
    fg_hue: float = 0.0
    bg_hue: float = 0.0
    font_family: str = "serif"
    thickness: float = 1.0
