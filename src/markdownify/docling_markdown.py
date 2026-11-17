"""DoclingDocument → markdown, keeping the code-point range of everything it writes, then words aligned to the source.

WHY our own serializer rather than docling's `export_to_markdown`: docling's serializer returns the markdown and the
items that contributed to it, but no offsets into the string (`SerializationResult.spans` has no ranges; research
1-converters.md, W1). Writing item by item is the only way to know which characters of the markdown came from which
item and table cell. The markup follows docling's own choices where they exist (GFM pipe tables, `#` headings, `-`
bullets) so the output reads the same.

What it writes:
- headings (`#` for the title, `##`… for section headers by level), paragraphs, captions, footnotes, list items
  (nested by their list groups), fenced code;
- tables as GFM pipe tables — or, when a cell spans rows or columns (GFM cannot hold that), as a raw HTML `<table>`
  with rowspan/colspan on a rectangular grid (`markdownify.tables`; CONTRACT §2); every cell is a `table_cell` block with
  docling's own row/col/span and its header `kind`;
- pictures as `figure` blocks with `represented: false` and their box, and no markdown (CONTRACT §3.1): the words
  inside them are counted as flagged, not lost;
- page headers and footers (docling's furniture layer) are left out and listed in `dropped`;
- a block's or cell's words in the order the page prints them (`reading_order`: lines by their boxes, top to bottom,
  left to right), when docling's text is exactly the source words inside its box in another order.

Then every markdown word is aligned (markdownify.align) to the source text units the caller supplies — pdfplumber words
for a PDF, OCR lines for an image — block by block: a cell's words against the units inside the cell's box, a
paragraph's against the units inside its box, then whatever is left per page.
"""

from __future__ import annotations

import bisect
import collections
import dataclasses
import html
import re
from collections.abc import Callable

from docling_core.types.doc import (CodeItem, ContentLayer, DocItemLabel, GroupLabel, ListItem, PictureItem,
                                    SectionHeaderItem, TableItem, TextItem, TitleItem)

from . import align
from .converters import Conversion
from .tables import GridCell, html_table

Box = tuple[float, float, float, float]

# Bullet glyphs that become markdown's "- " (docling reports the source's glyph as `ListItem.marker`).
_BULLETS = {"·", "•", "‧", "●", "▪", "■", "◦", "○", "–", "-", "*", "\uf0b7", "\uf0a7"}

# How far outside a docling box a source word's centre may sit and still count as inside it, as a fraction of the
# page. WHY: docling's layout boxes are model predictions and can clip a glyph at the edge.
_MARGIN = 0.004


@dataclasses.dataclass
class SourceText:
    """One unit of source text the markdown can link to: a pdfplumber word, or an OCR line."""
    text: str                          # as the converter compares it (after `fold`, if any)
    raw: str                           # as the source has it
    page: int
    box: Box                           # 0–1 of the page, origin top-left
    char_boxes: list[Box] | None = None  # one per code point of `text`; None → interpolated along the box


def box_of_characters(unit: SourceText, start: int, end: int) -> Box:
    """The box of unit.text[start:end]: the union of its characters' boxes, or a horizontal slice of the unit's box
    in proportion to the characters when the source has no character boxes (OCR lines)."""
    if unit.char_boxes:
        boxes = unit.char_boxes[start:end] or unit.char_boxes
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    x0, y0, x1, y1 = unit.box
    length = max(len(unit.text), 1)
    width = x1 - x0
    return (x0 + width * start / length, y0, x0 + width * end / length, y1)


def union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def rounded(box: Box) -> list[float]:
    return [round(min(max(value, 0.0), 1.0), 5) for value in box]


# --- markdown escaping ------------------------------------------------------------------------------------------
_ENTITY_START = re.compile(r"&(#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")


def escape_text(text: str, *, in_table: bool = False) -> str:
    """Escape what markdown would otherwise read as syntax, so the text a reader sees is the source's text and
    markdownify.align counts it as content. Kept minimal (readability): only characters that are syntax where they sit."""
    out: list[str] = []
    for index, character in enumerate(text):
        before = text[index - 1] if index else " "
        after = text[index + 1] if index + 1 < len(text) else " "
        if character in "\\*`[]~":
            out.append("\\" + character)
        elif character == "_" and not (before.isalnum() and after.isalnum()):
            out.append("\\_")
        elif character == "<" and (after.isalpha() or after in "/!"):
            out.append("\\<")
        elif character == "&" and _ENTITY_START.match(text, index):
            out.append("\\&")
        elif character == "|" and in_table:
            out.append("\\|")
        else:
            out.append(character)
    escaped = "".join(out)
    # Line-start syntax: a heading, quote, bullet, ordered-list number or thematic break the source never meant.
    if re.match(r"[#>]|[-+](\s|$)|=+$|-+$", escaped):
        escaped = "\\" + escaped
    else:
        number = re.match(r"(\d{1,9})([.)])(\s|$)", escaped)
        if number:
            escaped = number.group(1) + "\\" + escaped[len(number.group(1)):]
    return escaped


def escape_html_text(text: str) -> str:
    """Text inside a raw-HTML cell: HTML-escaped, and the characters markdown tooling reads as emphasis or escapes
    written as numeric entities (a raw HTML block is not markdown-processed, so a backslash would show)."""
    escaped = html.escape(text, quote=False)
    for character in "\\*_`[]~":
        escaped = escaped.replace(character, f"&#{ord(character)};")
    return escaped


def squash(text: str) -> str:
    return " ".join(text.split())


# --- the writer -------------------------------------------------------------------------------------------------
class _Writer:
    def __init__(self):
        self.parts: list[str] = []
        self.length = 0

    def write(self, text: str) -> tuple[int, int]:
        start = self.length
        self.parts.append(text)
        self.length += len(text)
        return start, self.length

    def separate(self, separator: str) -> None:
        if self.length:
            self.write(separator)

    def text(self) -> str:
        return "".join(self.parts)


class _Serializer:
    def __init__(self, document, fold: Callable[[str], str] | None, units: list[SourceText] | None = None):
        self.document = document
        self.units_by_page: dict[int, list[SourceText]] = {}
        # Leaf block id → the source units its text was put in reading order from, in that order (see `_ground_words`).
        self.unit_order: dict[str, list[SourceText]] = {}
        self.pending_order: dict[int, list[SourceText]] = {}
        for unit in units or []:
            self.units_by_page.setdefault(unit.page, []).append(unit)
        self.fold = fold or (lambda text: text)
        self.writer = _Writer()
        self.blocks: list[dict] = []
        self.dropped: list[dict] = []
        self.counters: dict[str, int] = {}
        self.emitted: set[str] = set()
        self.previous_was_list = False
        self.list_numbers: dict[str, int] = {}
        # Per leaf block (one that owns markdown words directly): its block dict and the groundings to search.
        self.leaves: list[dict] = []

    # --- helpers ---
    def new_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}-{self.counters[kind]}"

    def page_size(self, page_no: int) -> tuple[float, float]:
        size = self.document.pages[page_no].size
        return size.width, size.height

    def normalized(self, bbox, page_no: int) -> Box:
        width, height = self.page_size(page_no)
        top_left = bbox.to_top_left_origin(page_height=height)
        return (top_left.l / width, top_left.t / height, top_left.r / width, top_left.b / height)

    def groundings(self, item) -> list[dict]:
        return [{"page": prov.page_no, "box": rounded(self.normalized(prov.bbox, prov.page_no))}
                for prov in getattr(item, "prov", []) or []]

    def item_text(self, item) -> str:
        """The item's text as the PDF has it: docling's `orig`, not its sanitised `text`. WHY: `text` rewrites
        characters (U+2212 minus → "-", curly quotes → straight) and strips list markers, and the markdown must say
        what the source says (CONTRACT §4.1; Zach: render stored text truthfully). `text` only when `orig` is empty
        (formulas without enrichment keep their PDF text in `orig`)."""
        raw = getattr(item, "orig", None) or getattr(item, "text", "") or ""
        text = self.in_reading_order(squash(self.fold(raw)), self.groundings(item), item)
        if isinstance(item, ListItem) and not getattr(item, "enumerated", False):
            marker = (getattr(item, "marker", "") or "").strip()
            if marker in _BULLETS and text.startswith(marker):
                # A bullet glyph becomes the markdown bullet: syntax, so its source glyph is dropped on purpose.
                text = text[len(marker):].lstrip()
        return text

    def in_reading_order(self, text: str, groundings: list[dict], owner) -> str:
        """`text` with its words in the order the page shows them, when it is exactly the source words inside its
        box in another order; otherwise `text` unchanged.

        WHY: docling joins the lines of a block or cell in the order its OCR or text cells came, not the order they
        are printed. JMC's servo list prints its headers on stacked lines ("Power" / "supply" / "voltage" / "(V)")
        and the markdown said "voltage supply Power (V)", so the extractor's label check dropped correct values
        (2026-10-29). Only a reordering of the source's own words is accepted: the markdown never says a word the
        source does not."""
        if len(groundings) != 1 or not text:
            return text
        grounding = groundings[0]
        inside = [unit for unit in self.units_by_page.get(grounding["page"], []) if _inside(unit, grounding)]
        if not inside or collections.Counter(text.split()) != collections.Counter(
                word for unit in inside for word in unit.text.split()):
            return text
        ordered = reading_order(inside)
        self.pending_order[id(owner)] = ordered
        return " ".join(unit.text.strip() for unit in ordered)

    def take_order(self, owner, block: dict) -> None:
        ordered = self.pending_order.pop(id(owner), None)
        if ordered is not None:
            self.unit_order[block["id"]] = ordered

    def cell_text(self, cell, page_no) -> str:
        return self.in_reading_order(squash(self.fold(cell.text or "")), self.cell_grounding(cell, page_no), cell)

    def cell_grounding(self, cell, page_no) -> list[dict]:
        if cell.bbox is None or page_no is None:
            return []
        return [{"page": page_no, "box": rounded(self.normalized(cell.bbox, page_no))}]

    def begin_block(self, list_item: bool) -> None:
        if list_item and self.previous_was_list:
            self.writer.separate("\n")
        else:
            self.writer.separate("\n\n")
        self.previous_was_list = list_item

    # --- items ---
    def run(self) -> None:
        layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
        for item, level in self.document.iterate_items(included_content_layers=layers):
            reference = item.self_ref
            if reference in self.emitted:
                continue
            self.emitted.add(reference)
            if item.content_layer == ContentLayer.FURNITURE:
                self.drop(item)
            elif isinstance(item, TableItem):
                self.table(item)
            elif isinstance(item, PictureItem):
                self.picture(item)
            elif isinstance(item, (TextItem, ListItem, SectionHeaderItem, TitleItem, CodeItem)):
                self.text_block(item)
            # Other items (key-value regions, forms) carry no text of their own in docling's PDF pipeline.

    def drop(self, item) -> None:
        label = getattr(item, "label", None)
        reason = {DocItemLabel.PAGE_HEADER: "header", DocItemLabel.PAGE_FOOTER: "footer"}.get(label, "boilerplate")
        for grounding in self.groundings(item):
            self.dropped.append({"grounding": grounding, "reason": reason})

    def text_block(self, item) -> None:
        text = self.item_text(item)
        if not text:
            return
        label = item.label
        is_list = isinstance(item, ListItem)
        self.begin_block(is_list)
        if isinstance(item, TitleItem) or label == DocItemLabel.TITLE:
            kind, prefix = "heading", "# "
        elif isinstance(item, SectionHeaderItem):
            kind, prefix = "heading", "#" * min(int(getattr(item, "level", 1) or 1) + 1, 6) + " "
        elif is_list:
            kind, prefix = "list_item", self.list_prefix(item)
        elif isinstance(item, CodeItem) or label == DocItemLabel.CODE:
            kind = "code"
        elif label == DocItemLabel.CAPTION:
            kind, prefix = "caption", ""
        elif label == DocItemLabel.FOOTNOTE:
            kind, prefix = "footnote", ""
        else:
            kind, prefix = "paragraph", ""
        if kind == "code":
            code = self.fold(item.text or "")
            start, _ = self.writer.write("```\n")
            self.writer.write(code.rstrip("\n").replace("```", "`​``") + "\n")
            _, end = self.writer.write("```")
        else:
            start, _ = self.writer.write(prefix)
            _, end = self.writer.write(escape_text(text))
        block = {"id": self.new_id(kind), "type": kind, "range": {"start": start, "end": end},
                 "grounding": self.groundings(item), "represented": True}
        self.blocks.append(block)
        self.leaves.append(block)
        if kind == "code":
            self.pending_order.pop(id(item), None)   # code is written from docling's text as is
        else:
            self.take_order(item, block)

    def list_prefix(self, item) -> str:
        depth = 0
        parent = item.parent.resolve(self.document) if item.parent else None
        list_parent = parent
        while parent is not None and getattr(parent, "self_ref", "#") != "#/body":
            if getattr(parent, "label", None) in (GroupLabel.LIST, GroupLabel.ORDERED_LIST):
                depth += 1
            parent = parent.parent.resolve(self.document) if getattr(parent, "parent", None) else None
        indent = "    " * max(depth - 1, 0)
        if getattr(item, "enumerated", False):
            key = getattr(list_parent, "self_ref", "")
            self.list_numbers[key] = self.list_numbers.get(key, 0) + 1
            return f"{indent}{self.list_numbers[key]}. "
        return f"{indent}- "

    def captions_of(self, item) -> None:
        for caption_ref in getattr(item, "captions", []) or []:
            caption = caption_ref.resolve(self.document)
            if caption.self_ref in self.emitted:
                continue
            self.emitted.add(caption.self_ref)
            self.text_block(caption)

    def picture(self, item) -> None:
        offset = self.writer.length
        self.blocks.append({"id": self.new_id("figure"), "type": "figure", "range": {"start": offset, "end": offset},
                            "grounding": self.groundings(item), "represented": False})
        self.previous_was_list = False
        self.captions_of(item)

    def table(self, item) -> None:
        data = item.data
        cells = sorted(data.table_cells, key=lambda cell: (cell.start_row_offset_idx, cell.start_col_offset_idx))
        groundings = self.groundings(item)
        if not cells:
            # A table docling saw but could not read (no text layer under it): flagged, never silently empty.
            offset = self.writer.length
            self.blocks.append({"id": self.new_id("table"), "type": "table", "range": {"start": offset, "end": offset},
                                "grounding": groundings, "represented": False, "rows": data.num_rows,
                                "cols": data.num_cols, "children": []})
            self.previous_was_list = False
            self.captions_of(item)
            return
        self.begin_block(False)
        table_block = {"id": self.new_id("table"), "type": "table", "range": None, "grounding": groundings,
                       "represented": True, "rows": data.num_rows, "cols": data.num_cols, "children": []}
        self.blocks.append(table_block)
        page_no = item.prov[0].page_no if item.prov else None
        has_row_headers = any(cell.row_header for cell in cells)
        merged = any(cell.row_span > 1 or cell.col_span > 1 for cell in cells)
        start = self.writer.length
        cell_blocks = self.html_table(cells, data, page_no, has_row_headers) if merged else \
            self.gfm_table(cells, data, page_no, has_row_headers)
        table_block["range"] = {"start": start, "end": self.writer.length}
        table_block["children"] = [cell["id"] for cell in cell_blocks]
        self.captions_of(item)

    def cell_block(self, cell, page_no, has_row_headers, start, end) -> dict:
        if cell.column_header and cell.row_header:
            kind = "stubHead"
        elif cell.column_header:
            kind = "stubHead" if has_row_headers and cell.start_col_offset_idx == 0 else "columnHeader"
        elif cell.row_header or cell.row_section:
            kind = "rowHeader"
        else:
            kind = "content"
        grounding = self.cell_grounding(cell, page_no)
        block = {"id": self.new_id("table_cell"), "type": "table_cell", "range": {"start": start, "end": end},
                 "row": cell.start_row_offset_idx, "col": cell.start_col_offset_idx, "rowspan": cell.row_span,
                 "colspan": cell.col_span, "kind": kind, "grounding": grounding}
        self.blocks.append(block)
        self.leaves.append(block)
        self.take_order(cell, block)
        return block

    def gfm_table(self, cells, data, page_no, has_row_headers) -> list[dict]:
        by_position = {(cell.start_row_offset_idx, cell.start_col_offset_idx): cell for cell in cells}
        blocks = []
        for row in range(data.num_rows):
            if row:
                self.writer.write("\n")
            for col in range(data.num_cols):
                self.writer.write("| ")
                cell = by_position.get((row, col))
                text = escape_text(self.cell_text(cell, page_no), in_table=True) if cell else ""
                start, end = self.writer.write(text)
                if cell is not None:
                    blocks.append(self.cell_block(cell, page_no, has_row_headers, start, end))
                self.writer.write(" ")
            self.writer.write("|")
            if row == 0:
                self.writer.write("\n" + "|" + "|".join(["---"] * data.num_cols) + "|")
        return blocks

    def html_table(self, cells, data, page_no, has_row_headers) -> list[dict]:
        # WHY markdownify.tables: docling's cells are not always a grid (a slot nothing covers, a span over another
        # cell); written row by row they shifted LRS-350's model names one column off their values.
        grid_cells = [GridCell(cell.start_row_offset_idx, cell.start_col_offset_idx, cell.row_span, cell.col_span,
                               bool(cell.column_header or cell.row_header),
                               escape_html_text(self.cell_text(cell, page_no))) for cell in cells]
        markup, ranges = html_table(grid_cells, data.num_rows, data.num_cols)
        offset, _ = self.writer.write(markup)
        return [self.cell_block(cell, page_no, has_row_headers, offset + start, offset + end)
                for cell, (start, end) in zip(cells, ranges)]


# --- serialise and align ----------------------------------------------------------------------------------------
def serialize(document, units: list[SourceText], *, fold: Callable[[str], str] | None = None,
              pages: list[dict] | None = None, folded_note: str = "symbol_font") -> Conversion:
    """Markdown, blocks, words and dropped regions for `document`, with every word aligned to `units`."""
    serializer = _Serializer(document, fold, units)
    serializer.run()
    markdown = serializer.writer.text() + "\n"
    if pages is None:
        pages = [{"page": page_no, "width": round(page.size.width, 2), "height": round(page.size.height, 2), "unit": "pt"}
                 for page_no, page in sorted(document.pages.items())]
    _cover_dropped_words(serializer.dropped, units)
    words, joins = _ground_words(markdown, serializer.leaves, units, folded_note, serializer.dropped, pages,
                                 serializer.unit_order)
    markdown = _glue(markdown, serializer.blocks, words, joins)
    return Conversion(markdown=markdown, blocks=serializer.blocks, words=words, dropped=serializer.dropped, pages=pages)


def _cover_dropped_words(dropped: list[dict], units: list[SourceText]) -> None:
    """Grow each dropped region (a page header or footer) to the full boxes of the source words inside it.

    WHY: docling's furniture box hugs the glyphs, while a word's box spans its font's ascent and descent; a header
    word whose box only half overlaps the region would read as lost rather than dropped on purpose."""
    for region in dropped:
        grounding = region["grounding"]
        inside = [unit.box for unit in units if _inside(unit, grounding)]
        if inside:
            grounding["box"] = rounded(union([tuple(grounding["box"]), *inside]))


# Two markdown words that matched touching characters of ONE source word are one word in the source when the gap
# between those characters is under this fraction of the glyph height.
_GLUE_GAP = 0.2


def _glue(markdown: str, blocks: list[dict], rows: list[dict], joins: list[int]) -> str:
    """Remove the space docling put inside a source word, and merge the two word rows it split.

    WHY: docling spaces glyphs that sit on another baseline or are kerned apart — "Rated torque 2" for the text
    layer's "torque²" (a superscript footnote mark), "i ndus t r i a l" for "industrial" — so the markdown said
    something the source does not, and a table's row label no longer read as the source's label (harness case
    07-footnotes, header paths 0.25). The text layer decides: no space there, no space here.
    """
    for index in sorted(joins, reverse=True):
        left, right = rows[index], rows[index + 1]
        start, end = left["range"]["end"], right["range"]["start"]
        removed = end - start
        markdown = markdown[:start] + markdown[end:]

        def shift(position: int) -> int:
            return position - removed if position >= end else min(position, start)

        for block in blocks:
            block["range"] = {"start": shift(block["range"]["start"]), "end": shift(block["range"]["end"])}
        for row in rows[index + 1:]:
            row["range"] = {"start": shift(row["range"]["start"]), "end": shift(row["range"]["end"])}
        merged = {"range": {"start": left["range"]["start"], "end": right["range"]["end"]},
                  "grounding": {"page": left["grounding"]["page"],
                                "box": rounded(union([left["grounding"]["box"], right["grounding"]["box"]]))},
                  "score": min(left["score"], right["score"])}
        note = left.get("note") or right.get("note")
        if note:
            merged["note"] = note
        rows[index:index + 2] = [merged]
    return markdown


def reading_order(units: list[SourceText]) -> list[SourceText]:
    """Units as a reader reads them: grouped into lines by their boxes — one line when they overlap vertically by
    at least half the shorter one's height, so a superscript stays on its line — lines top to bottom, units in a
    line left to right."""
    lines: list[tuple[list[float], list[SourceText]]] = []   # ([top, bottom], units)
    for unit in sorted(units, key=lambda unit: (unit.box[1] + unit.box[3]) / 2):
        top, bottom = unit.box[1], unit.box[3]
        for extent, members in lines:
            overlap = min(bottom, extent[1]) - max(top, extent[0])
            if overlap >= 0.5 * min(bottom - top, extent[1] - extent[0]):
                members.append(unit)
                extent[0], extent[1] = min(top, extent[0]), max(bottom, extent[1])
                break
        else:
            lines.append(([top, bottom], [unit]))
    lines.sort(key=lambda line: line[0][0])
    return [unit for _extent, members in lines for unit in sorted(members, key=lambda unit: unit.box[0])]


def _inside(unit: SourceText, grounding: dict) -> bool:
    if unit.page != grounding["page"]:
        return False
    x0, y0, x1, y1 = grounding["box"]
    center_x = (unit.box[0] + unit.box[2]) / 2
    center_y = (unit.box[1] + unit.box[3]) / 2
    return x0 - _MARGIN <= center_x <= x1 + _MARGIN and y0 - _MARGIN <= center_y <= y1 + _MARGIN


def _ground_words(markdown: str, leaves: list[dict], units: list[SourceText], folded_note: str,
                  dropped: list[dict], pages: list[dict],
                  unit_order: dict[str, list[SourceText]] | None = None) -> tuple[list[dict], list[int]]:
    """Word rows, and the indices i where word i and word i+1 should be one word (see `_glue`).

    A leaf whose text the serializer put in reading order offers its units to the aligner in that same order.
    WHY: the aligner pairs the two sequences in order; offered in OCR order, JMC's "2500:" line was pinned to the
    "2500C:" line printed below it, and the other way round."""
    unit_index = {id(unit): j for j, unit in enumerate(units)}
    unit_order = unit_order or {}
    words = align.markdown_words(markdown)
    starts = [word.start for word in words]

    # Which leaf block owns each word (leaves never overlap: cells are leaves, their table is not).
    owner: list[dict | None] = [None] * len(words)
    for leaf in leaves:
        low = bisect.bisect_left(starts, leaf["range"]["start"])
        for index in range(low, len(words)):
            if words[index].start >= leaf["range"]["end"]:
                break
            owner[index] = leaf

    by_page: dict[int, list[int]] = {}
    for index, unit in enumerate(units):
        by_page.setdefault(unit.page, []).append(index)

    def candidates(leaf: dict) -> list[int]:
        if leaf["id"] in unit_order:
            return [unit_index[id(unit)] for unit in unit_order[leaf["id"]]]
        found = []
        for grounding in leaf.get("grounding", []):
            found.extend(j for j in by_page.get(grounding["page"], []) if _inside(units[j], grounding))
        return sorted(set(found))

    leaf_words: dict[str, list[int]] = {}
    for index, leaf in enumerate(owner):
        if leaf is not None:
            leaf_words.setdefault(leaf["id"], []).append(index)

    # Cells first (the tightest boxes), then the rest in reading order; then per page, whatever is left.
    ordered = sorted(leaves, key=lambda leaf: 0 if leaf["type"] == "table_cell" else 1)
    groups = [(leaf_words.get(leaf["id"], []), candidates(leaf)) for leaf in ordered]
    page_words: dict[int, list[int]] = {}
    for index, leaf in enumerate(owner):
        pages_of_leaf = [grounding["page"] for grounding in (leaf or {}).get("grounding", [])]
        if pages_of_leaf:
            page_words.setdefault(pages_of_leaf[0], []).append(index)
    for page_no in sorted(page_words):
        groups.append((page_words[page_no], by_page.get(page_no, [])))

    alignments = align.align_word_spans([word.text for word in words], [unit.text for unit in units],
                                        groups=groups, residual=False)
    # The bullet glyphs that became markdown's "- ": left out on purpose, so listed as dropped, not lost.
    used = {j for alignment in alignments for j, _start, _end in alignment.sources}
    for leaf in leaves:
        if leaf["type"] != "list_item":
            continue
        for j in candidates(leaf):
            if j not in used and units[j].text.strip() and set(units[j].text.strip()) <= _BULLETS:
                dropped.append({"grounding": {"page": units[j].page, "box": rounded(units[j].box)},
                                "reason": "boilerplate"})
                used.add(j)

    rows = []
    for word, alignment in zip(words, alignments):
        row = {"range": {"start": word.start, "end": word.end}, "grounding": None, "score": 0.0}
        if alignment.sources:
            page_no = units[alignment.sources[0][0]].page
            boxes = [box_of_characters(units[j], start, end) for j, start, end in alignment.sources
                     if units[j].page == page_no]
            row["grounding"] = {"page": page_no, "box": rounded(union(boxes))}
            row["score"] = alignment.score
            folded = any(units[j].text[start:end] != units[j].raw[start:end] for j, start, end in alignment.sources)
            if folded:
                # WHY capped below 1.0: the converter decoded the text (a symbol font's private-use code points
                # into the letters they draw), so the markdown no longer equals the text layer byte for byte —
                # flagged, never hidden (CONTRACT §3.2).
                row["score"] = min(row["score"], 0.99)
                row["note"] = folded_note
        rows.append(row)

    aspect = {page["page"]: page["width"] / page["height"] for page in pages if page.get("height")}
    joins = []
    for index in range(len(words) - 1):
        left, right = alignments[index], alignments[index + 1]
        if not (left.sources and right.sources) or markdown[words[index].end:words[index + 1].start].strip(" "):
            continue
        (left_unit, _left_start, left_end), (right_unit, right_start, _right_end) = left.sources[-1], right.sources[0]
        unit = units[left_unit]
        if left_unit != right_unit or right_start != left_end or not unit.char_boxes or left_end >= len(unit.char_boxes):
            continue
        before, after = unit.char_boxes[left_end - 1], unit.char_boxes[left_end]
        gap = (after[0] - before[2]) * aspect.get(unit.page, 1.0)   # in page-height units, like the glyph height
        height = max(before[3] - before[1], after[3] - after[1])
        if gap < _GLUE_GAP * height:
            joins.append(index)
    return rows, joins
