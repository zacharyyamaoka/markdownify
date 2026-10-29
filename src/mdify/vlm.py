"""Pipeline B, "AI only": a vision model looks at each page image, writes the markdown AND says where every piece sits.

    uv run python -m mdify.vlm <source> --out <bundle> [--model claude|codex] [--coordinates pixel|unit]

WHY it exists: Zach wants pipeline A (parse first, then align: docling + the text layer) measured against a model
alone on the round-trip harness. The research predicts B loses on location (ParseBench, OCRBench v2 spotting: Opus
4.6 7.4, GPT-5.2 2.2; research 1-converters.md, W2), but that was measured on older models than his CLIs run, so B
gets one honest measured run. Nothing here reads the text layer to *place* a word — only to *grade* it afterwards.

- **Render:** `mdify.vlm_pages` — PDF at 192 DPI, an image as is, HTML as one full-page screenshot.
- **Ask:** `mdify.vlm_llm` — one call per page through `claude -p` or `codex exec`, structured output: blocks in
  reading order, each with its type, its printed lines (text + box) or, for a table, its cells (row, col, spans,
  header kind, text, box). Coordinates are asked in image pixels by default; `--coordinates unit` asks for 0–1
  fractions instead (measured on one case before the full run: see the lane's report).
- **Assemble:** the markdown is written block by block (headings, paragraphs, lists, GFM tables, an HTML `<table>`
  when a cell spans, `<sup>`), every block and cell range recorded, figures/plots as `represented: false` blocks,
  running headers/footers/navigation as `dropped` regions.
- **Words:** each markdown word's box is its slice of the line (or cell) box it came from, in proportion to its
  characters. WHY by character count: the model gives line boxes, not word or character boxes, and asking a
  general model for one box per word multiplies the output (and the error) for no measured gain — the same split
  the image path uses for OCR lines (`docling_markdown.box_of_characters`).
- **Place (`--place words`, pipeline "AB"):** the line box becomes a search window instead of the answer: each word
  is aligned to the source's own words inside it — the text layer, else RapidOCR's word boxes (`mdify.vlm_place`).
- **Score:** a word's `score` is its text against the text-layer characters under its box (`pdf.text_under`, the
  checker's own reading), when the page has a text layer. When it has none (a PNG, an HTML screenshot) the score is
  `null` with `note: "unverified"` — NOT 1.0: nothing independent of the model says the word is right.

The ledger and KPIs come from `mdify.ledger`, exactly as for pipeline A; the inventory is pipeline A's own module's
(pdfplumber words for a PDF, none for an image, the visible text nodes for HTML — whose source ranges a page box
cannot overlap, so an HTML bundle from B shows every source word lost: B grounds on the screenshot, not the DOM).

Unlike the other converters, `convert` writes files: the page images and every call's raw event stream go under
`<bundle>/vlm/` so a run can be audited (the lane's brief: keep every raw event stream under the bundle).
"""

from __future__ import annotations

import argparse
import bisect
import copy
import dataclasses
import hashlib
import json
import pathlib
import re
import shutil
import sys
import time

from . import align
from .converters import Conversion, Source, SourceWord
from .docling_markdown import escape_html_text, escape_text, rounded

MEDIA_TYPES = ("application/pdf", "image/png", "image/jpeg", "text/html", "application/xhtml+xml")
PIPELINE = "B"
COORDINATES = ("pixel", "unit")
DEFAULT_COORDINATES = "pixel"
PLACES = ("chars", "words")      # a word's box: its character-count slice of the line, or the source's own word
ATTEMPTS = 2                     # a page whose call fails (rate limit, no structured reply) is asked once more

TEXT_TYPES = ("heading", "paragraph", "list_item", "caption", "footnote")
FIGURE_TYPES = ("figure", "plot", "diagram")
DROP_REASONS = {"page_header": "header", "page_footer": "footer", "navigation": "navigation"}
BLOCK_TYPES = TEXT_TYPES + ("table",) + FIGURE_TYPES + tuple(DROP_REASONS)
CELL_KINDS = ("columnHeader", "rowHeader", "stubHead", "content")
_BULLETS = ("•", "·", "‧", "●", "▪", "■", "◦", "○", "–", "-", "*", "", "")
_ORDERED = re.compile(r"(\d{1,9})([.)])\s+")
# <sup>/<sub> are the only markup the model may write; they survive escaping as private-use placeholders.
_INLINE_TAGS = {"<sup>": "", "</sup>": "", "<sub>": "", "</sub>": ""}


# --- the request ------------------------------------------------------------------------------------------------
def _box_schema(description: str) -> dict:
    return {"type": "array", "items": {"type": "number"}, "description": description}


def output_schema() -> dict:
    """Strict (every property required, no extras), so `codex --output-schema` accepts it as well as claude."""
    line = {"type": "object", "additionalProperties": False, "required": ["text", "box"],
            "properties": {"text": {"type": "string"}, "box": _box_schema("[x0, y0, x1, y1] of this line")}}
    cell = {"type": "object", "additionalProperties": False,
            "required": ["row", "col", "rowspan", "colspan", "kind", "text", "box"],
            "properties": {"row": {"type": "integer"}, "col": {"type": "integer"},
                           "rowspan": {"type": "integer"}, "colspan": {"type": "integer"},
                           "kind": {"type": "string", "enum": list(CELL_KINDS)},
                           "text": {"type": "string"},
                           "box": _box_schema("[x0, y0, x1, y1] of the cell's text")}}
    block = {"type": "object", "additionalProperties": False, "required": ["type", "level", "box", "lines", "cells"],
             "properties": {"type": {"type": "string", "enum": list(BLOCK_TYPES)},
                            "level": {"type": "integer", "description": "heading level 1-6; 0 for other blocks"},
                            "box": _box_schema("[x0, y0, x1, y1] of the whole block"),
                            "lines": {"type": "array", "items": line},
                            "cells": {"type": "array", "items": cell}}}
    return {"type": "object", "additionalProperties": False, "required": ["blocks"],
            "properties": {"blocks": {"type": "array", "items": block}}}


SYSTEM_PROMPT = """You convert one page image of a born-digital document (a datasheet, a spec sheet, a web page) \
into markdown blocks, and you say exactly where each piece sits on the page. A program checks every word you write \
against the page and every box against where that text really is, so copy the text exactly and place the boxes \
exactly.

Blocks, in reading order (a multi-column layout: finish a column before starting the next):
- heading: a title or section heading; `level` 1 for the page's title, 2 for a section, 3 for a subsection.
- paragraph; caption (a figure's or table's caption); footnote (a note at the foot of the page or under a table); \
list_item (one per bullet or numbered item).
- table: every non-empty cell in `cells`: row and col (0-based, in the grid of the table's finest rows and \
columns), rowspan and colspan (1 unless the cell is merged across rows or columns), kind (columnHeader: a header \
cell above the data; stubHead: the header cell above the row labels; rowHeader: a row's label; content: the rest), \
its text, and the box of its text.
- figure, plot, diagram: a picture, a chart with axes, a drawing. Give only its box; never transcribe the text \
inside it (axis labels, ticks, legends). Its caption is a separate caption block.
- page_header, page_footer, navigation: running headers and footers (company name, document title, revision, page \
number, web address) and menus. Give them like paragraphs; they are left out of the markdown on purpose.

Text: exactly as printed, character for character. Keep the page's symbols (N·m, Ω, °C, –, ×, µ, ±), its \
non-Latin scripts, spelling, capitalisation and punctuation. Write no markdown syntax, no escaping and no words that \
are not printed. The only markup allowed: <sup>…</sup> around superscripts (footnote marks, exponents) and \
<sub>…</sub> around subscripts. A list item's text starts with its bullet or number as printed (•, -, 1.).

Lines: every block except tables and figures lists each printed line in `lines`, top to bottom, with its exact text \
and its box; joining the lines' texts with one space gives the block's text. Tables and figures have `lines: []`; \
every block except a table has `cells: []`.

{coordinates}

Reply with the JSON object only."""

_COORDINATE_RULES = {
    "pixel": "Boxes: every box is [x0, y0, x1, y1] in integer pixels of this image ({width} × {height} px): (x0, y0) "
             "its top-left corner, (x1, y1) its bottom-right, the origin at the image's top-left corner, x to the "
             "right, y down. A line's box runs from the left edge of its first character to the right edge of its "
             "last, and from the top of its tallest letters to the bottom of its descenders; a block's box encloses "
             "its lines.",
    "unit": "Boxes: every box is [x0, y0, x1, y1] as fractions of the image's width and height, from 0 to 1 with 4 "
            "decimals (the image is {width} × {height} px; x0 = left edge ÷ {width}, y0 = top edge ÷ {height}): "
            "(x0, y0) its top-left corner, (x1, y1) its bottom-right, the origin at the image's top-left corner, x "
            "to the right, y down. A line's box runs from the left edge of its first character to the right edge "
            "of its last, and from the top of its tallest letters to the bottom of its descenders; a block's box "
            "encloses its lines.",
}


def system_prompt(coordinates: str, width: int, height: int) -> str:
    return SYSTEM_PROMPT.replace("{coordinates}",
                                 _COORDINATE_RULES[coordinates].format(width=width, height=height))


def page_prompt(page: int, pages: int, width: int, height: int) -> str:
    return (f"Page {page} of {pages}. The image is {width} × {height} pixels. Convert it: every block in reading "
            "order, with its lines (or cells) and their boxes.")


# --- boxes ------------------------------------------------------------------------------------------------------
def normalised_box(box, coordinates: str, width: int, height: int) -> list[float] | None:
    """The model's box → [x0, y0, x1, y1], 0–1 of the page, origin top-left; None when it is not four numbers.
    Corners given in the wrong order are put in order; values are clamped to the page (nothing else is fixed: a
    box in the wrong units stays wrong, and the harness counts it misplaced)."""
    if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
        return None
    x0, y0, x1, y1 = (float(v) for v in box)
    if coordinates == "pixel":
        x0, x1, y0, y1 = x0 / width, x1 / width, y0 / height, y1 / height
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return rounded((x0, y0, x1, y1))


# --- assembling the markdown ------------------------------------------------------------------------------------
@dataclasses.dataclass
class _Segment:
    """A stretch of markdown written from one line or cell of the model's reply, and the box the model gave it."""
    start: int
    end: int
    page: int
    box: list[float] | None
    lead: int = 0                # characters printed before the markdown text (a stripped bullet) inside the box
    block: int = 0               # which reply block it came from (`--place words` falls back to the whole block)


def _protect(text: str) -> str:
    for tag, placeholder in _INLINE_TAGS.items():
        text = text.replace(tag, placeholder)
    return text


def _restore(text: str) -> str:
    for tag, placeholder in _INLINE_TAGS.items():
        text = text.replace(placeholder, tag)
    return text


def _escaped(text: str, *, first: bool = True, in_table: bool = False) -> str:
    """Markdown-escaped text, keeping <sup>/<sub>. A continuation line is escaped as if a space preceded it, so a
    line-start rule (a leading "-" or "1.") never fires in the middle of a paragraph."""
    text = _protect(" ".join(text.split()))
    if first:
        return _restore(escape_text(text, in_table=in_table))
    return _restore(escape_text(" " + text, in_table=in_table)[1:])


def _html_escaped(text: str) -> str:
    return _restore(escape_html_text(_protect(" ".join(text.split()))))


def _list_marker(text: str) -> tuple[str, str, int]:
    """(markdown prefix, text without its printed marker, characters stripped)."""
    stripped = text.lstrip()
    for bullet in _BULLETS:
        if stripped.startswith(bullet) and (len(stripped) == len(bullet) or stripped[len(bullet)].isspace()):
            rest = stripped[len(bullet):].lstrip()
            return "- ", rest, len(text) - len(rest)
    ordered = _ORDERED.match(stripped)
    if ordered:
        rest = stripped[ordered.end():]
        return f"{ordered.group(1)}{ordered.group(2)} ", rest, len(text) - len(rest)
    return "- ", text, 0


class _Assembler:
    def __init__(self):
        self.parts: list[str] = []
        self.length = 0
        self.blocks: list[dict] = []
        self.dropped: list[dict] = []
        self.segments: list[_Segment] = []
        self.counters: dict[str, int] = {}
        self.previous_was_list = False
        self.block_number = 0

    def write(self, text: str) -> tuple[int, int]:
        start = self.length
        self.parts.append(text)
        self.length += len(text)
        return start, self.length

    def new_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}-{self.counters[kind]}"

    def begin_block(self, list_item: bool) -> None:
        if self.length:
            self.write("\n" if list_item and self.previous_was_list else "\n\n")
        self.previous_was_list = list_item

    def markdown(self) -> str:
        text = "".join(self.parts)
        return text + "\n" if text else text

    # --- blocks ---
    def add(self, block: dict, page: int, to_box) -> None:
        self.block_number += 1
        kind = block.get("type")
        box = to_box(block.get("box"))
        grounding = [{"page": page, "box": box}] if box else []
        lines = [line for line in block.get("lines") or [] if (line.get("text") or "").strip()]
        if kind in DROP_REASONS:
            boxes = [to_box(line.get("box")) for line in lines] or [box]
            for region in [region for region in boxes if region] or ([box] if box else []):
                self.dropped.append({"grounding": {"page": page, "box": region}, "reason": DROP_REASONS[kind]})
            return
        if kind in FIGURE_TYPES:
            offset = self.length
            self.blocks.append({"id": self.new_id(kind), "type": kind, "range": {"start": offset, "end": offset},
                                "grounding": grounding, "represented": False})
            self.previous_was_list = False
            return
        if kind == "table":
            self.table(block, page, to_box, grounding)
            return
        if kind not in TEXT_TYPES or not lines:
            return
        self.begin_block(kind == "list_item")
        prefix, lead = "", 0
        first_text = lines[0]["text"]
        if kind == "heading":
            level = min(max(int(block.get("level") or 1), 1), 6)
            prefix = "#" * level + " "
        elif kind == "list_item":
            prefix, first_text, lead = _list_marker(first_text)
            if lead:
                # The printed bullet became markdown's "- ": syntax, so its slice of the line is dropped on purpose
                # (pipeline A does the same with the bullet glyph's own box).
                first_box = to_box(lines[0].get("box"))
                if first_box:
                    total = lead + len(" ".join(first_text.split()))
                    width = first_box[2] - first_box[0]
                    self.dropped.append({"grounding": {"page": page, "box": rounded(
                        (first_box[0], first_box[1], first_box[0] + width * lead / max(total, 1), first_box[3]))},
                        "reason": "boilerplate"})
        start, _ = self.write(prefix)
        for number, line in enumerate(lines):
            text = first_text if number == 0 else line["text"]
            if number:
                self.write(" ")
            segment_start, segment_end = self.write(_escaped(text, first=number == 0))
            self.segments.append(_Segment(segment_start, segment_end, page, to_box(line.get("box")),
                                          lead if number == 0 else 0, self.block_number))
        self.blocks.append({"id": self.new_id(kind), "type": kind, "range": {"start": start, "end": self.length},
                            "grounding": grounding, "represented": True})

    def table(self, block: dict, page: int, to_box, grounding: list[dict]) -> None:
        cells = []
        for cell in block.get("cells") or []:
            try:
                row, col = int(cell["row"]), int(cell["col"])
                rowspan, colspan = max(int(cell.get("rowspan") or 1), 1), max(int(cell.get("colspan") or 1), 1)
            except (KeyError, TypeError, ValueError):
                continue
            if row < 0 or col < 0:
                continue
            kind = cell.get("kind") if cell.get("kind") in CELL_KINDS else "content"
            cells.append({"row": row, "col": col, "rowspan": rowspan, "colspan": colspan, "kind": kind,
                          "text": cell.get("text") or "", "box": to_box(cell.get("box"))})
        if not cells:
            offset = self.length
            self.blocks.append({"id": self.new_id("table"), "type": "table", "range": {"start": offset, "end": offset},
                                "grounding": grounding, "represented": False, "children": []})
            self.previous_was_list = False
            return
        cells.sort(key=lambda cell: (cell["row"], cell["col"]))
        rows = max(cell["row"] + cell["rowspan"] for cell in cells)
        cols = max(cell["col"] + cell["colspan"] for cell in cells)
        self.begin_block(False)
        table_block = {"id": self.new_id("table"), "type": "table", "range": None, "grounding": grounding,
                       "represented": True, "rows": rows, "cols": cols, "children": []}
        self.blocks.append(table_block)
        start = self.length
        # Two cells on one slot cannot both sit in a GFM table; the HTML grid keeps both (`tables.html_table`).
        merged = any(cell["rowspan"] > 1 or cell["colspan"] > 1 for cell in cells) or \
            len({(cell["row"], cell["col"]) for cell in cells}) < len(cells)
        cell_blocks = self.html_table(cells, rows, cols, page) if merged else self.gfm_table(cells, rows, cols, page)
        table_block["range"] = {"start": start, "end": self.length}
        table_block["children"] = [cell["id"] for cell in cell_blocks]

    def cell_block(self, cell: dict, page: int, start: int, end: int) -> dict:
        block = {"id": self.new_id("table_cell"), "type": "table_cell", "range": {"start": start, "end": end},
                 "row": cell["row"], "col": cell["col"], "rowspan": cell["rowspan"], "colspan": cell["colspan"],
                 "kind": cell["kind"], "grounding": [{"page": page, "box": cell["box"]}] if cell["box"] else []}
        self.blocks.append(block)
        self.segments.append(_Segment(start, end, page, cell["box"], block=self.block_number))
        return block

    def gfm_table(self, cells: list[dict], rows: int, cols: int, page: int) -> list[dict]:
        by_position = {}
        for cell in cells:
            by_position.setdefault((cell["row"], cell["col"]), cell)   # a duplicate position keeps the first
        blocks = []
        for row in range(rows):
            if row:
                self.write("\n")
            for col in range(cols):
                self.write("| ")
                cell = by_position.get((row, col))
                start, end = self.write(_escaped(cell["text"], in_table=True) if cell else "")
                if cell is not None:
                    blocks.append(self.cell_block(cell, page, start, end))
                self.write(" ")
            self.write("|")
            if row == 0:
                self.write("\n" + "|" + "|".join(["---"] * cols) + "|")
        return blocks

    def html_table(self, cells: list[dict], rows: int, cols: int, page: int) -> list[dict]:
        """WHY `mdify.tables.html_table`: a model's cell list is not always a grid either (a missing cell, two cells
        on one slot, a span over a neighbour); written row by row, the cells after a gap slide one column left and
        a value reads under the wrong model. The shared builder keeps every cell at its own (row, col) and fills
        or clips the rest; the index keeps the model's own spans."""
        from .tables import GridCell, html_table

        grid_cells = [GridCell(row=cell["row"], col=cell["col"], rowspan=cell["rowspan"], colspan=cell["colspan"],
                               header=cell["kind"] in ("columnHeader", "stubHead"),
                               text=_html_escaped(cell["text"])) for cell in cells]
        markup, ranges = html_table(grid_cells, rows, cols)
        offset, _ = self.write(markup)
        return [self.cell_block(cell, page, offset + start, offset + end)
                for cell, (start, end) in zip(cells, ranges)]


def _visible(markdown: str, mask: bytes, start: int, end: int) -> int:
    """Printed characters in markdown[start:end]: content plus spaces, never syntax or tags."""
    return sum(1 for position in range(start, end)
               if mask[position] == align.CONTENT or markdown[position].isspace())


def ground_words(markdown: str, segments: list[_Segment]) -> list[dict]:
    """One word row per markdown word (CONTRACT §3.2): its slice of its segment's box, by character count."""
    mask = align.content_mask(markdown)
    segments = sorted((segment for segment in segments if segment.end > segment.start), key=lambda s: s.start)
    starts = [segment.start for segment in segments]
    rows = []
    for word in align.markdown_words(markdown):
        row = {"range": {"start": word.start, "end": word.end}, "grounding": None, "score": None}
        position = bisect.bisect_right(starts, word.start) - 1
        segment = segments[position] if position >= 0 else None
        if segment is not None and word.start < segment.end and segment.box:
            total = segment.lead + _visible(markdown, mask, segment.start, segment.end)
            before = segment.lead + _visible(markdown, mask, segment.start, word.start)
            through = segment.lead + _visible(markdown, mask, segment.start, min(word.end, segment.end))
            x0, y0, x1, y1 = segment.box
            width = x1 - x0
            row["grounding"] = {"page": segment.page, "box": rounded(
                (x0 + width * before / max(total, 1), y0, x0 + width * through / max(total, 1), y1))}
        rows.append(row)
    return rows


def assemble(replies: list[tuple[int, dict, int, int]], coordinates: str,
             units: tuple[list, str] | None = None) -> Conversion:
    """[(page, structured reply, image width px, image height px)] → the Conversion (pages left to the caller).
    `units` = (source words, their kind) moves each word onto the source words inside its line (`--place words`,
    `mdify.vlm_place`); without them a word is its character-count slice of the line box."""
    assembler = _Assembler()
    for page, output, width, height in replies:
        def to_box(box, width=width, height=height):
            return normalised_box(box, coordinates, width, height)
        for block in (output or {}).get("blocks") or []:
            if isinstance(block, dict):
                assembler.add(block, page, to_box)
    markdown = assembler.markdown()
    words = ground_words(markdown, assembler.segments)
    if units is not None:
        from . import vlm_place

        vlm_place.place(markdown, words, assembler.segments, *units)
    return Conversion(markdown=markdown, blocks=assembler.blocks, words=words, dropped=assembler.dropped, pages=[])


# --- scoring ----------------------------------------------------------------------------------------------------
def score_words(conversion: Conversion, source: Source) -> None:
    """Each linked word against the text-layer characters under its box, as `mdify check` reads them; `null` and
    `note: "unverified"` where there is no text layer to read. WHY never 1.0 by default: a score is a claim that
    the source says what the markdown says, and for a page image only the model says so. A word `--place words`
    put on an OCR word keeps the aligner's score against it, noted "ocr" (an engine apart from the model, but not
    the source itself)."""
    for word in conversion.words:
        placed = word.pop("placed", None)
        if placed == "ocr":
            word["note"] = "ocr"
            continue
        word["score"] = None
        if word.get("grounding"):
            word["note"] = "unverified"
    if source.media_type != "application/pdf":
        return
    from . import pdf

    if not pdf.has_text_layer(source.path):
        return
    pdf._score_against_text_layer(conversion, source)
    for word in conversion.words:
        if word.get("score") is not None and word.get("note") == "unverified":
            del word["note"]


# --- the protocol (mdify.converters) ----------------------------------------------------------------------------
def _pipeline_a(source: Source):
    """Pipeline A's module for this media type: B is graded against the same inventory and text layer as A."""
    import importlib

    from .bundle import CONVERTERS

    media_type = "text/html" if source.media_type == "application/xhtml+xml" else source.media_type
    return importlib.import_module(CONVERTERS[media_type])


def inventory(source: Source) -> list[SourceWord] | None:
    return _pipeline_a(source).inventory(source)


def text_under(source: Source, grounding: dict) -> str | None:
    return _pipeline_a(source).text_under(source, grounding)


@dataclasses.dataclass
class Run:
    """What a conversion asked and got, for `vlm/run.json` and the index's `tool`."""
    backend: str
    coordinates: str
    models: list[str]
    calls: list[dict]
    place: str = "chars"
    placement: dict | None = None     # `--place words`: how many words the source's words placed


def convert(source: Source, *, backend: str = "claude", coordinates: str = DEFAULT_COORDINATES,
            run_dir: pathlib.Path | None = None, model: str | None = None, place: str = "chars") -> Conversion:
    conversion, _run = convert_with_run(source, backend=backend, coordinates=coordinates, run_dir=run_dir,
                                        model=model, place=place)
    return conversion


def convert_with_run(source: Source, *, backend: str = "claude", coordinates: str = DEFAULT_COORDINATES,
                     run_dir: pathlib.Path | None = None, model: str | None = None,
                     place: str = "chars") -> tuple[Conversion, Run]:
    from . import vlm_llm, vlm_pages
    from .cache import Cache

    if coordinates not in COORDINATES:
        raise ValueError(f"coordinates must be one of {COORDINATES}")
    if place not in PLACES:
        raise ValueError(f"place must be one of {PLACES}")
    run_dir = pathlib.Path(run_dir) if run_dir else Cache().bundle_dir(source.sha256) / f"vlm-{backend}"
    run_dir.mkdir(parents=True, exist_ok=True)
    images = vlm_pages.render(source, run_dir)
    schema = output_schema()
    replies, calls, models = [], [], []
    for image in images:
        reply = None
        for attempt in range(1, ATTEMPTS + 1):
            call_dir = run_dir / f"page-{image.page}" / f"call-{attempt}"
            reply = vlm_llm.ask(backend, image.path, system_prompt(coordinates, image.width_px, image.height_px),
                                page_prompt(image.page, len(images), image.width_px, image.height_px), schema,
                                call_dir, model=model)
            calls.append({"page": image.page, "attempt": attempt, "model": reply.model, "seconds": reply.seconds,
                          "error": reply.error, "cli_version": reply.cli_version,
                          "dir": str(call_dir.relative_to(run_dir))})
            if reply.output is not None and reply.error is None:
                break
        if reply.model and reply.model not in models:
            models.append(reply.model)
        if reply.output is None or reply.error is not None:
            _write_run(run_dir, Run(backend, coordinates, models, calls))
            raise RuntimeError(f"page {image.page}: {reply.error} (raw events under {reply.run_dir})")
        replies.append((image.page, reply.output, image.width_px, image.height_px))
    units, placement = None, None
    if place == "words":
        from . import vlm_place

        units = vlm_place.source_units(source, images)
    conversion = assemble(replies, coordinates, units)
    conversion.pages = [image.size for image in images]
    if units is not None:
        placement = {"source": units[1], "source_words": len(units[0]), "words": len(conversion.words),
                     "placed": sum(1 for word in conversion.words if word.get("placed"))}
    score_words(conversion, source)
    run = Run(backend, coordinates, models, calls, place, placement)
    _write_run(run_dir, run)
    return conversion, run


def _write_run(run_dir: pathlib.Path, run: Run) -> None:
    (run_dir / "run.json").write_text(json.dumps(dataclasses.asdict(run), ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")


# --- a bundle (mdify.bundle.build, with this converter and its options) -----------------------------------------
def build(target: str, out: pathlib.Path | None = None, *, backend: str = "claude",
          coordinates: str = DEFAULT_COORDINATES, model: str | None = None, place: str = "chars", cache=None,
          log=None):
    """`mdify.bundle.build` for pipeline B. WHY not bundle.build itself: it picks the converter by media type
    (pipeline A's), and B's backend and coordinate options are not part of the protocol. Everything else — the
    fetch record, index, ledger and KPIs — goes through the same core functions."""
    from . import bundle
    from . import ledger as ledger_module
    from .cache import Cache, extension_of, write_json

    cache = cache or Cache()
    sha256, media_type, url = bundle.resolve(target, cache, log)
    if media_type not in MEDIA_TYPES:
        raise bundle.NoConverter(f"pipeline B cannot read {media_type}")
    directory = pathlib.Path(out) if out else cache.bundle_dir(sha256)
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / f"source.{extension_of(media_type)}"
    if directory.resolve() != cache.bundle_dir(sha256).resolve():
        if not source_path.is_file():
            shutil.copyfile(cache.source_path(sha256), source_path)
        shutil.copyfile(cache.bundle_dir(sha256) / "source.json", directory / "source.json")
    source = Source(path=source_path, sha256=sha256, media_type=media_type, url=url)

    started = time.perf_counter()
    conversion, run = convert_with_run(source, backend=backend, coordinates=coordinates, run_dir=directory / "vlm",
                                       model=model, place=place)
    # WHY cold, no model load: every page is a fresh CLI process; there is nothing to warm.
    timing = {"seconds": round(time.perf_counter() - started, 3), "cold": True, "model_load_seconds": 0.0}

    markdown = conversion.markdown.replace("\r\n", "\n").replace("\r", "\n")
    markdown_bytes = markdown.encode("utf-8")
    index = bundle.index_of(conversion, source, PIPELINE, hashlib.sha256(markdown_bytes).hexdigest())
    index["tool"].update({"backend": run.backend, "models": run.models, "coordinates": run.coordinates,
                          "place": run.place})
    if place == "words":
        index["tool"]["pipeline"] = "AB"
    ledger = ledger_module.compute(_scored_for_ledger(index), markdown, inventory(source), f"{__name__}.inventory")
    for entry in ledger["markdown"]["inexact_words"]:
        if entry.get("note") == "unverified":
            entry["score"] = None
    kpis = ledger_module.kpis_of(index, ledger, timing)

    (directory / "document.md").write_bytes(markdown_bytes)
    write_json(directory / "index.json", index)
    write_json(directory / "ledger.json", ledger)
    write_json(directory / "kpis.json", kpis)
    return bundle.Built(directory=directory, kpis=kpis)


def _scored_for_ledger(index: dict) -> dict:
    """The index with each unverified word's `null` score read as 0.0 — `mdify.ledger` compares scores with < 1.0
    — so the ledger lists every unverified word among the inexact ones (flagged, never hidden); `build` writes the
    null back into the ledger's rows."""
    copied = copy.copy(index)
    copied["words"] = [{**word, "score": 0.0} if word.get("score") is None else word for word in index["words"]]
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mdify.vlm", description="Pipeline B: a vision model writes the "
                                     "markdown and says where each piece sits (one call per page).")
    parser.add_argument("source", help="a local file or an http(s) URL (PDF, PNG, JPEG, HTML)")
    parser.add_argument("--out", type=pathlib.Path, help="write the bundle here instead of the cache")
    parser.add_argument("--model", choices=("claude", "codex"), default="claude", help="which logged-in CLI to ask")
    parser.add_argument("--model-id", help="the model the CLI runs (default: claude-opus-5-5 / gpt-6.1-sol)")
    parser.add_argument("--coordinates", choices=COORDINATES, default=DEFAULT_COORDINATES,
                        help="ask for boxes in image pixels or in 0–1 fractions")
    parser.add_argument("--place", choices=PLACES, default="chars", help="a word's box: its character-count slice "
                        "of the model's line box (pipeline B), or the source's own word inside that line — text "
                        "layer, else OCR (pipeline AB)")
    arguments = parser.parse_args(argv)

    from .bundle import NoConverter
    from .cache import display_path
    from .fetch import FetchError, FetchRefused

    try:
        built = build(arguments.source, arguments.out, backend=arguments.model, coordinates=arguments.coordinates,
                      model=arguments.model_id, place=arguments.place,
                      log=lambda message: print(message, file=sys.stderr))
    except (FetchRefused, FetchError, NoConverter, FileNotFoundError, RuntimeError) as error:
        print(f"mdify.vlm: {arguments.source}: {error}", file=sys.stderr)
        return 1
    kpis = built.kpis
    print(display_path(built.directory))
    print(f"  coverage {kpis['coverage']}  traceability {kpis['traceability']}  lost {kpis['silently_lost']}  "
          f"unlinked {kpis['unlinked']}  inexact {kpis['inexact_words']}  {kpis['time']['pages']} pages in "
          f"{kpis['time']['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
