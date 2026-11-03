from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from parsers.indra.types import HeadingPrototype

logger = logging.getLogger(__name__)


@dataclass
class DocumentHeadingTracker:
    """Tracks document-wide headings, hierarchical breadcrumbs, and cross-page state."""

    default_title: str = "Document"
    document_toc: list[dict[str, Any]] = field(default_factory=list)
    heading_mismatches: list[dict[str, Any]] = field(default_factory=list)
    active_stack: dict[int, str] = field(default_factory=dict)
    chapter_prototypes: dict[str, list[HeadingPrototype]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def get_initial_stack_for_page(self, page_number: int) -> dict[int, str]:
        # Scan document_toc for headings occurring before page_number
        stack: dict[int, str] = {}
        for entry in self.document_toc:
            p = entry.get("page", 1)
            if p >= page_number:
                break
            lvl = int(entry.get("level", 1))
            stack[lvl] = entry.get("title", "")
            for d in [k for k in list(stack) if k > lvl]:
                stack.pop(d, None)
        return stack

    def get_page_toc_entries(self, page_number: int) -> list[dict[str, Any]]:
        return [e for e in self.document_toc if e.get("page") == page_number]

    def get_mismatches_for_page(self, page_number: int) -> list[dict[str, Any]]:
        return [m for m in self.heading_mismatches if m.get("page") == page_number]


_DOCUMENT_TRACKERS: dict[str, DocumentHeadingTracker] = {}


def get_or_create_document_tracker(
    file_path: Path,
    default_title: str = "",
    reset: bool = False,
    target_page: int = 1,
    full_scan: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> DocumentHeadingTracker:
    key = str(file_path.resolve())
    if reset or key not in _DOCUMENT_TRACKERS:
        root_title = default_title or file_path.stem
        tracker = DocumentHeadingTracker(default_title=root_title)
        _DOCUMENT_TRACKERS[key] = tracker
    return _DOCUMENT_TRACKERS[key]
