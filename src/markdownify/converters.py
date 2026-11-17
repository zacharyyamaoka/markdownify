"""The seam every converter implements (CONTRACT.md §2–5). One module per kind of source: `markdownify.pdf`, `markdownify.html`,
`markdownify.image`, later `markdownify.vlm` (pipeline B). The core (`bundle`, `ledger`, `check`, `cli`) knows only this protocol.

A converter module exposes:

    MEDIA_TYPES: tuple[str, ...]                     # e.g. ("application/pdf",)
    PIPELINE: str                                    # "A" (parse first, then align) or "B" (model only)

    def convert(source: Source) -> Conversion
        Read the source bytes, return the markdown, its blocks and words (CONTRACT §3) and the regions left out on
        purpose. Never writes files: the core writes the bundle.

    def inventory(source: Source) -> list[SourceWord]
        The source's own word list, read WITHOUT the converter's code path, so the ledger (§5) cannot be graded by
        the thing it grades. PDF: pdfplumber words. HTML: the page's visible text nodes.

    def text_under(source: Source, grounding: dict) -> str
        The source text a grounding points at (PDF: text-layer characters inside the box; HTML: the text at
        source_range). `markdownify check` compares it to the markdown word (invariant 1).
"""

from __future__ import annotations

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True)
class Source:
    path: pathlib.Path          # the cached bytes (source.<ext>)
    sha256: str
    media_type: str
    url: str | None = None      # where it was fetched from, when it was


@dataclasses.dataclass(frozen=True)
class SourceWord:
    text: str
    grounding: dict             # {"page", "box"} or {"html": {"source_range": …}}


@dataclasses.dataclass
class Conversion:
    markdown: str
    blocks: list[dict]          # CONTRACT §3.1
    words: list[dict]           # CONTRACT §3.2
    dropped: list[dict]         # [{"grounding", "reason"}]
    pages: list[dict]           # [{"page", "width", "height", "unit"}]; [] for HTML
