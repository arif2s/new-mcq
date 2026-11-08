from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BoundingBox:
    page_no: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_list(self) -> list[float | int]:
        return [self.page_no, round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]


@dataclass(slots=True)
class ParsedBlock:
    block_id: str
    text: str
    page_no: int = 1
    block_type: str = "paragraph"
    heading_level: int | None = None
    bbox: BoundingBox | None = None
    confidence: float = 1.0
    matched_rules: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HeadingNode:
    title: str
    level: int
    page_no: int = 1
    bbox: BoundingBox | None = None
    confidence: float = 1.0
    matched_rules: list[str] = field(default_factory=list)
    content_blocks: list[ParsedBlock] = field(default_factory=list)
    children: list[HeadingNode] = field(default_factory=list)

    def to_toc_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "page_no": self.page_no,
            "bbox": self.bbox.to_list() if self.bbox else None,
            "confidence": round(self.confidence, 3),
            "matched_rules": self.matched_rules,
            "children": [child.to_toc_dict() for child in self.children],
        }


@dataclass(slots=True)
class DocumentSection:
    heading: str
    level: int
    page_start: int
    page_end: int
    text: str
    bbox: BoundingBox | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    source_path: str
    title: str = ""
    blocks: list[ParsedBlock] = field(default_factory=list)
    toc_tree: list[HeadingNode] = field(default_factory=list)
    sections: list[DocumentSection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalize_heading_hierarchy(self) -> None:
        """Clamp skipped heading levels (e.g., H1 -> H4 becomes H1 -> H2) across blocks and sections."""
        last_level = 0
        for block in self.blocks:
            if block.heading_level is not None and block.heading_level > 0:
                if last_level == 0:
                    block.heading_level = 1
                elif block.heading_level > last_level + 1:
                    block.heading_level = last_level + 1
                block.heading_level = min(6, max(1, block.heading_level))
                last_level = block.heading_level

        last_sec_level = 0
        for sec in self.sections:
            if sec.level > 0:
                if last_sec_level == 0:
                    sec.level = 1
                elif sec.level > last_sec_level + 1:
                    sec.level = last_sec_level + 1
                sec.level = min(6, max(1, sec.level))
                last_sec_level = sec.level

    def to_markdown(self) -> str:
        self.normalize_heading_hierarchy()
        lines: list[str] = []
        if self.blocks:
            for blk in self.blocks:
                text = blk.text.strip()
                if not text:
                    continue
                if blk.heading_level and 1 <= blk.heading_level <= 6:
                    lines.append(f"{'#' * blk.heading_level} {text}")
                else:
                    lines.append(text)
            return "\n\n".join(lines)

        for sec in self.sections:
            lvl = min(6, max(1, sec.level or 1))
            if sec.heading:
                lines.append(f"{'#' * lvl} {sec.heading.strip()}")
            if sec.text and sec.text.strip():
                lines.append(sec.text.strip())
        return "\n\n".join(lines)

    def to_toc_dict(self) -> list[dict[str, Any]]:
        return [node.to_toc_dict() for node in self.toc_tree]


def clamp_heading_levels(headings: list[dict[str, Any]], level_key: str = "level") -> list[dict[str, Any]]:
    """Clamp dictionary-based heading lists so H1 -> H4 is normalized to H1 -> H2."""
    last_level = 0
    for item in headings:
        lvl = int(item.get(level_key, 1) or 1)
        if last_level == 0:
            lvl = 1
        elif lvl > last_level + 1:
            lvl = last_level + 1
        lvl = min(6, max(1, lvl))
        item[level_key] = lvl
        last_level = lvl
    return headings
