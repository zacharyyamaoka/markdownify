"""Forward render: ground-truth markdown → a Chrome-printed PDF, a page image and a web page, each with a truth
bundle that says exactly where every markdown word landed. "FK on markdown" (Zach, 2026-10-29); the pipeline under
test is the IK, and score.py compares the two.

    uv run python harness/render.py 04-multi-model
    uv run python harness/render.py --all

The chain (research 3-evaluation.md, "Ground-truth-box chain", measured there on one page):

1. `pandoc -f commonmark_x-smart+sourcepos` tags every markdown token with `data-pos="line:col-line:col"`, so each
   rendered character can be traced back to its code point in the markdown. `smart` is off so `--` stays `--`.
2. The page template: one fixed Letter page (816 × 1056 CSS px), embedded fonts, ligatures off. WHY ligatures off:
   with them on, Chrome prints "ffi" as one glyph and the character join breaks (measured: 158 PDF chars for 166
   DOM chars).
3. Playwright measures every character with `Range.getClientRects()`, prints the page with `page.pdf()` and takes
   a 2× screenshot of it (the image arm).
4. Every DOM character is joined to the PDF text-layer character that drew it, and the case FAILS LOUDLY unless
   every character pairs up with x error under 0.05 pt (PubTabNet's rule: verify the truth by re-extracting it).
   The stored word box is the PDF text-layer box, not the DOM box. WHY: the two use different vertical
   conventions (DOM content area vs PDF em box; a perfect answer scores IoU 0.78 across them), and a PDF parser
   reports text-layer boxes, so the truth should be in that convention.

Born-digital sources only (Zach, 2026-10-29): there is no degraded or scanned arm. Each case is one page; a case
whose content overflows the page fails, because the image arm takes one image per source.

Per case, under harness/out/<case>/ (gitignored):
    pdf/  png/  html/       one truth bundle per arm, in CONTRACT.md's format: source.<ext>, source.json,
                            document.md, index.json, ledger.json, kpis.json — plus truth.json (the source-word
                            inventory with each word's fate and the ladder kinds; harness-only, not a bundle file)
    print.html              the measured page, kept for debugging
    selfcheck.json          the join's result

Case directives are HTML comments on their own line in source.md, removed from the truth markdown:
    <!-- harness:plot -->          the torque–speed plot from case.json's "plot" series (represented: false)
    <!-- harness:columns -->       start a two-column flow;  <!-- harness:end-columns -->  ends it
case.json may also hold "running_header" / "running_footer" (pdf and png only), "nav" (html only) — the intended
drops — and "complex_tables": true (the ladder counts the case's tables as complex).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import html as html_lib
import json
import logging
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pdfplumber
from lxml import etree

from harness import mdtext, tables

HERE = pathlib.Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
OUT_DIR = HERE / "out"

PAGE_WIDTH_PX, PAGE_HEIGHT_PX = 816, 1056          # US Letter at 96 CSS px per inch
PX_TO_PT = 72 / 96
PAGE_WIDTH_PT, PAGE_HEIGHT_PT = PAGE_WIDTH_PX * PX_TO_PT, PAGE_HEIGHT_PX * PX_TO_PT
PNG_SCALE = 2
X_TOLERANCE_PT = 0.05      # the self-check's horizontal gate (the probe measured 0.014 pt max)
Y_TOLERANCE_PT = 4.0       # vertical centres differ by a fixed convention offset; this only rules out another line

DROP_REASONS = {"header": "header", "footer": "footer", "nav": "navigation"}
FLAG_KINDS = {"plot": "plot"}

FONT_FACES = """
@font-face { font-family: "Harness Sans"; font-weight: 400;
  src: url("file:///usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"); }
@font-face { font-family: "Harness Sans"; font-weight: 700;
  src: url("file:///usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"); }
@font-face { font-family: "Harness CJK";
  src: local("Noto Sans CJK SC"), url("file:///usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"); }
"""

BODY_STYLE = """
html, body { margin: 0; padding: 0; background: #fff; }
body { font: 13px/1.45 "Harness Sans", "Harness CJK", sans-serif; color: #111;
  font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
h1 { font-size: 22px; margin: 0 0 12px; }
h2 { font-size: 16px; margin: 14px 0 8px; }
p { margin: 0 0 10px; }
ul { margin: 0 0 10px; padding-left: 22px; }
table { border-collapse: collapse; margin: 0 0 12px; }
th, td { border: 1px solid #999; padding: 3px 8px; text-align: left; vertical-align: top; }
th { background: #eef1f5; }
sup { font-size: 0.7em; line-height: 0; }
.columns { column-count: 2; column-gap: 28px; }
.columns h2 { margin-top: 0; }
.columns table, .columns li { break-inside: avoid; }
figure.plot { margin: 0 0 8px; }
"""

PRINT_STYLE = FONT_FACES + """
@page { size: 8.5in 11in; margin: 0; }
""" + BODY_STYLE + """
section.page { position: relative; width: 816px; height: 1056px; overflow: hidden; box-sizing: border-box;
  padding: 64px; }
.content { height: 928px; overflow: hidden; }
header.running, footer.running { position: absolute; left: 64px; right: 64px; display: flex;
  justify-content: space-between; font-size: 10px; color: #555; }
header.running { top: 26px; border-bottom: 1px solid #bbb; padding-bottom: 4px; }
footer.running { bottom: 26px; border-top: 1px solid #bbb; padding-top: 4px; }
"""

WEB_STYLE = BODY_STYLE.replace('"Harness Sans", "Harness CJK"', '"DejaVu Sans", "Noto Sans CJK SC"') + """
body { display: flex; gap: 32px; padding: 24px; }
nav { width: 180px; font-size: 12px; }
nav ul { list-style: none; padding-left: 0; }
nav ul ul { padding-left: 12px; margin: 2px 0 8px; }
main { max-width: 760px; }
"""

MEASURE_JS = """
() => {
  const pageBox = document.querySelector('section.page').getBoundingClientRect();
  const rel = q => [q.left - pageBox.left, q.top - pageBox.top, q.right - pageBox.left, q.bottom - pageBox.top];
  const slots = {};
  for (const el of document.querySelectorAll('[data-t]')) {
    const node = el.firstChild;
    if (!node || node.nodeType !== 3 || el.childNodes.length !== 1) { slots[el.dataset.t] = null; continue; }
    const chars = [];
    for (let i = 0; i < node.data.length; i++) {
      if (/\\s/.test(node.data[i])) { chars.push(null); continue; }
      const range = document.createRange();
      range.setStart(node, i); range.setEnd(node, i + 1);
      const rects = Array.from(range.getClientRects()).filter(q => q.width > 0 && q.height > 0);
      chars.push(rects.length ? rel(rects[0]) : null);
    }
    slots[el.dataset.t] = {text: node.data, chars};
  }
  const boxes = {};
  for (const el of document.querySelectorAll('[data-b]')) boxes['b' + el.dataset.b] = rel(el.getBoundingClientRect());
  for (const el of document.querySelectorAll('[data-region]'))
    boxes['r' + el.dataset.region] = rel(el.getBoundingClientRect());
  const content = document.querySelector('.content');
  return {slots, boxes, overflow: content.scrollHeight - content.clientHeight,
          page: [pageBox.width, pageBox.height]};
}
"""

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"address", "article", "aside", "div", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
              "header", "li", "main", "nav", "ol", "p", "section", "table", "tbody", "td", "th", "thead", "tr", "ul"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SVG_TAGS = {"svg", "text", "tspan", "line", "polyline", "g", "path", "rect"}

DIRECTIVE_LINE = re.compile(r"^<!-- harness:([a-z-]+) -->\n(?:[ \t]*\n)?", re.M)
DATA_POS = re.compile(r"(\d+):(\d+)-(\d+):(\d+)")
RAW_TAG_SPAN = re.compile(r'<span\s+data-pos="[^"]*">(</?[A-Za-z][^<>]*>)</span>')
TAG_OR_COMMENT = re.compile(r"<!--.*?-->|<[^>]*>", re.S)


class CaseFailure(Exception):
    """The forward render could not prove its own truth: the case is unusable and says why."""


@dataclasses.dataclass
class Slot:
    """One run of visible text: a text node of the page, wrapped in `<span data-t>` so the browser can measure it."""
    text: str
    offsets: list[int | None]      # per code point: offset into document.md, or None (whitespace, region text)
    region: str | None             # header | footer | nav | plot, or None for content


@dataclasses.dataclass
class Block:
    type: str
    element: object
    range: tuple[int, int]
    extra: dict


# --- markdown → document.md + pandoc tree ---------------------------------------------------------------------

def strip_directives(source: str) -> tuple[str, list[int], dict[str, list[int]]]:
    """document.md (the source without directive lines), the source → document offset map, directive positions."""
    offset_map = [0] * (len(source) + 1)
    pieces, positions = [], {}
    cursor = document_length = 0
    for match in DIRECTIVE_LINE.finditer(source):
        kept = source[cursor:match.start()]
        for index in range(len(kept)):
            offset_map[cursor + index] = document_length + index
        document_length += len(kept)
        pieces.append(kept)
        for index in range(match.start(), match.end()):
            offset_map[index] = document_length
        positions.setdefault(match.group(1), []).append(document_length)
        cursor = match.end()
    kept = source[cursor:]
    for index in range(len(kept) + 1):
        offset_map[cursor + index] = document_length + index
    pieces.append(kept)
    return "".join(pieces), offset_map, positions


def pandoc_tree(source_path: pathlib.Path) -> etree._Element:
    result = subprocess.run(["pandoc", "-f", "commonmark_x-smart+sourcepos", "-t", "html", str(source_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise CaseFailure(f"pandoc failed: {result.stderr.strip()}")
    # Pandoc wraps an inline raw tag (`<sup>`) in its own span, which unbalances the tree; unwrap those.
    fragment = RAW_TAG_SPAN.sub(r"\1", result.stdout)
    try:
        return etree.fromstring(f"<div class=\"content\">{fragment}</div>",
                                etree.XMLParser(remove_comments=False, resolve_entities=False))
    except etree.XMLSyntaxError as error:
        raise CaseFailure(f"pandoc's HTML is not well formed enough to measure: {error}") from error


def line_starts(text: str) -> list[int]:
    starts = [0]
    for index, character in enumerate(text):
        if character == "\n":
            starts.append(index + 1)
    return starts


def pos_range(value: str, starts: list[int]) -> tuple[int, int] | None:
    """A `data-pos` value ("f@3:1-4:1;5:1-9:1") as one source code point range (columns count code points)."""
    def offset(line: str, column: str) -> int:
        # Pandoc ends the last block at the line after the file's last line; that is the end of the file.
        line_index = int(line) - 1
        return starts[line_index] + int(column) - 1 if line_index < len(starts) else starts[-1]

    spans = [(offset(a, b), offset(c, d)) for a, b, c, d in DATA_POS.findall(value)]
    return (min(s for s, _ in spans), max(e for _, e in spans)) if spans else None


# --- plot -----------------------------------------------------------------------------------------------------

def plot_element(plot: dict) -> etree._Element:
    """An SVG torque–speed plot drawn from the stored series. Labels stay horizontal: a rotated glyph's DOM box is
    its rotated bounding rectangle, which no text layer reports, and the self-check would (rightly) fail it."""
    width, height = 480, 270
    left, right, top, bottom = 48, 16, 28, 34
    x_ticks, y_ticks = plot["x_ticks"], plot["y_ticks"]
    x_max, y_max = x_ticks[-1], y_ticks[-1]

    def x(value): return left + (width - left - right) * value / x_max
    def y(value): return height - bottom - (height - top - bottom) * value / y_max

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<line x1="{left}" y1="{y(0)}" x2="{width - right}" y2="{y(0)}" stroke="#333" stroke-width="1"/>',
             f'<line x1="{left}" y1="{y(0)}" x2="{left}" y2="{top}" stroke="#333" stroke-width="1"/>']
    for tick in x_ticks:
        parts.append(f'<line x1="{x(tick):.1f}" y1="{y(0)}" x2="{x(tick):.1f}" y2="{y(0) + 4}" stroke="#333"/>')
        parts.append(f'<text x="{x(tick):.1f}" y="{y(0) + 16}" text-anchor="middle" font-size="10">{tick}</text>')
    for tick in y_ticks:
        parts.append(f'<line x1="{left - 4}" y1="{y(tick):.1f}" x2="{left}" y2="{y(tick):.1f}" stroke="#333"/>')
        parts.append(f'<line x1="{left}" y1="{y(tick):.1f}" x2="{width - right}" y2="{y(tick):.1f}" '
                     f'stroke="#ddd"/>')
        parts.append(f'<text x="{left - 7}" y="{y(tick) + 3.5:.1f}" text-anchor="end" font-size="10">{tick}</text>')
    points = " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in plot["series"])
    parts.append(f'<polyline points="{points}" fill="none" stroke="#1f5fbf" stroke-width="2"/>')
    parts.append(f'<text x="{(left + width - right) / 2:.1f}" y="{height - 4}" text-anchor="middle" '
                 f'font-size="11">{html_lib.escape(plot["x_label"])}</text>')
    parts.append(f'<text x="{left}" y="{top - 12}" text-anchor="start" font-size="11">'
                 f'{html_lib.escape(plot["y_label"])}</text>')
    parts.append("</svg>")
    figure = etree.Element("figure", {"class": "plot", "data-region": "plot"})
    figure.append(etree.fromstring("".join(parts)))
    return figure


# --- the base tree: directives, text slots, blocks ------------------------------------------------------------

def apply_directives(root: etree._Element, case: dict) -> None:
    for comment in list(root.iter(etree.Comment)):
        match = re.fullmatch(r"\s*harness:([a-z-]+)\s*", comment.text or "")
        if not match:
            continue
        wrapper = comment.getparent()
        if wrapper is root:
            wrapper = comment
        name = match.group(1)
        if name == "plot":
            if "plot" not in case:
                raise CaseFailure("<!-- harness:plot --> needs a \"plot\" entry in case.json")
            replacement = plot_element(case["plot"])
        elif name in ("columns", "end-columns"):
            replacement = etree.Element("harness-marker", {"name": name})
        else:
            raise CaseFailure(f"unknown directive harness:{name}")
        replacement.tail = wrapper.tail
        wrapper.getparent().replace(wrapper, replacement)

    for start in list(root.iter("harness-marker")):
        if start.get("name") != "columns":
            continue
        parent = start.getparent()
        columns = etree.Element("div", {"class": "columns"})
        sibling = start.getnext()
        while sibling is not None and not (sibling.tag == "harness-marker" and sibling.get("name") == "end-columns"):
            following = sibling.getnext()
            columns.append(sibling)
            sibling = following
        parent.replace(start, columns)
        if sibling is not None:
            parent.remove(sibling)


def text_slots_in(root: etree._Element):
    """Every (element, 'text'|'tail') that holds non-whitespace text, in document order."""
    for element in root.iter():
        if not isinstance(element.tag, str):
            if element.tail and element.tail.strip():
                yield element, "tail"
            continue
        if element.text and element.text.strip():
            yield element, "text"
        if element is not root and element.tail and element.tail.strip():
            yield element, "tail"


def wrap_slots(root: etree._Element, slots: list[Slot], source: str, offset_map: list[int]) -> None:
    """Give every visible text run its slot: its document offsets (by walking its token's `data-pos` range) and
    a `<span data-t>` (`<tspan>` inside SVG) for the browser to measure."""
    starts = line_starts(source)
    in_tag = bytearray(len(source))
    for match in TAG_OR_COMMENT.finditer(source):
        for index in range(match.start(), match.end()):
            in_tag[index] = 1
    cursors: dict[int, int] = {}

    for element, slot_kind in list(text_slots_in(root)):
        host = element if slot_kind == "text" else element.getparent()
        text = element.text if slot_kind == "text" else element.tail
        region = next((a.get("data-region") for a in [host, *host.iterancestors()] if a.get("data-region")), None)
        owner = next((a for a in [host, *host.iterancestors()] if a.get("data-pos")), None)
        offsets: list[int | None] = []
        if region is not None:
            offsets = [None] * len(text)
        elif owner is None:
            raise CaseFailure(f"text with no markdown source and no region: {text.strip()!r}")
        else:
            owner_start, owner_end = pos_range(owner.get("data-pos"), starts)
            cursor = cursors.get(id(owner), owner_start)
            for character in text:
                if character.isspace():
                    offsets.append(None)
                    continue
                while cursor < owner_end and (in_tag[cursor] or source[cursor] != character):
                    cursor += 1
                if cursor >= owner_end:
                    raise CaseFailure(f"rendered {character!r} in {text.strip()!r} is not in its markdown token "
                                      f"{source[owner_start:owner_end]!r}")
                offsets.append(offset_map[cursor])
                cursor += 1
            cursors[id(owner)] = cursor
        if any(ord(character) > 0xFFFF for character in text):
            raise CaseFailure(f"astral-plane character in {text!r}: JS and Python would index it differently")

        slot_id = len(slots)
        slots.append(Slot(text, offsets, region))
        in_svg = host.tag in SVG_TAGS
        wrapper = etree.Element("tspan" if in_svg else "span", {"data-t": str(slot_id)})
        wrapper.text = text
        if slot_kind == "text":
            element.text = None
            element.insert(0, wrapper)
        else:
            element.tail = None
            parent = element.getparent()
            parent.insert(parent.index(element) + 1, wrapper)


def element_offsets(element, slots: list[Slot]) -> list[int]:
    return [offset for marker in element.iter() if marker.get("data-t") is not None
            for offset in slots[int(marker.get("data-t"))].offsets if offset is not None]


def collect_blocks(root, slots, source, offset_map, positions, case) -> list[Block]:
    """The blocks of CONTRACT §3.1, in document order, each tagged `data-b` for measurement."""
    starts = line_starts(source)
    blocks: list[Block] = []
    plot_positions = list(positions.get("plot", []))

    def source_range(element) -> tuple[int, int]:
        holder = element if element.get("data-pos") else None
        if holder is None and element.tag == "li":
            holder = next((child for child in element if child.get("data-pos")), None)
        if holder is None:
            holder = next((a for a in element.iterancestors() if a.get("data-pos")), None)
        start, end = pos_range(holder.get("data-pos"), starts)
        while end > start and source[end - 1].isspace():
            end -= 1
        return offset_map[start], offset_map[end]

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag
        if tag in HEADING_TAGS:
            blocks.append(Block("heading", element, source_range(element), {}))
        elif tag == "p":
            first = next(iter(element), None)
            is_footnote = not (element.text or "").strip() and first is not None and first.tag == "sup"
            blocks.append(Block("footnote" if is_footnote else "paragraph", element, source_range(element), {}))
        elif tag == "li":
            blocks.append(Block("list_item", element, source_range(element), {}))
        elif tag == "table":
            table = tables.table_from_element(element)
            complex_table = case.get("complex_tables") or any(c.rowspan > 1 or c.colspan > 1 for c in table.cells)
            table_block = Block("table", element, source_range(element),
                                {"ladder": "complex_table" if complex_table else "simple_table", "children": []})
            blocks.append(table_block)
            header_rows = table.header_rows()
            cell_elements = [cell for row in element.iter("tr") for cell in row if cell.tag in ("td", "th")]
            for cell_element, cell in zip(cell_elements, table.cells):
                offsets = element_offsets(cell_element, slots)
                if not offsets:
                    raise CaseFailure("empty table cell: the truth has no range for it")
                if cell.row < header_rows:
                    kind = "stubHead" if cell.col == 0 else "columnHeader"
                else:
                    kind = "rowHeader" if cell.col == 0 else "content"
                blocks.append(Block("table_cell", cell_element, (min(offsets), max(offsets) + 1),
                                    {"row": cell.row, "col": cell.col, "rowspan": cell.rowspan,
                                     "colspan": cell.colspan, "kind": kind, "table": table_block}))
        elif tag == "figure" and element.get("data-region") == "plot":
            position = plot_positions.pop(0)
            blocks.append(Block("plot", element, (position, position), {"represented": False}))

    counters: dict[str, int] = {}
    for index, block in enumerate(blocks):
        counters[block.type] = counters.get(block.type, 0) + 1
        block.extra["id"] = f"{block.type}-{counters[block.type]}"
        block.element.set("data-b", str(index))
    for block in blocks:
        if block.type == "table_cell":
            block.extra.pop("table").extra["children"].append(block.extra["id"])
    return blocks


# --- serialisation (print page and web page) ------------------------------------------------------------------

class Serializer:
    """Writes a tree as HTML text and records where each slot character and each element landed in it.

    WHY a serialiser of our own: the web arm's truth is code point ranges into the HTML string (CONTRACT §3.3),
    so the writer must know the offset of every character it writes, and lxml's writer does not report offsets.
    In web mode pandoc's token spans and the measuring spans are unwrapped and the harness's attributes removed,
    so the HTML a converter reads carries no trace of the truth.
    """

    HARNESS_ATTRIBUTES = {"data-pos", "data-t", "data-b", "data-region"}

    def __init__(self, web: bool):
        self.web = web
        self.parts: list[str] = []
        self.length = 0
        self.char_offsets: dict[int, list[int]] = {}          # slot id → html offset per character
        self.slot_xpath: dict[int, str] = {}
        self.element_range: dict[int, tuple[int, int, str]] = {}   # id(element) → (start, end, xpath)
        self.visible: list[tuple[str, int | None]] = []        # (character, html offset) of the page's text

    def write(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def text(self, text: str, slot_id: int | None, in_body: bool) -> None:
        offsets = []
        for character in text:
            offsets.append(self.length)
            if in_body:
                self.visible.append((character, self.length))
            self.write(html_lib.escape(character, quote=False))
        if slot_id is not None:
            self.char_offsets[slot_id] = offsets

    def unwrapped(self, element) -> bool:
        if not self.web or element.tag not in ("span", "tspan"):
            return False
        return element.get("data-t") is not None or set(element.attrib) <= {"data-pos"}

    def element(self, element, xpath: str, counters: dict[str, int], in_body: bool) -> None:
        if not isinstance(element.tag, str):
            if not self.web:
                self.write(f"<!--{element.text}-->")
            if element.tail:
                self.text(element.tail, None, in_body)
            return
        tag = element.tag
        in_body = in_body or tag == "body"
        if self.unwrapped(element):
            slot_id = int(element.get("data-t")) if element.get("data-t") is not None else None
            if slot_id is not None:
                self.slot_xpath[slot_id] = xpath
            if element.text:
                # Pandoc wraps its HTML at 72 columns inside these spans; a web page would hold a plain space.
                text = element.text if element.text.strip() else " "
                self.text(text, slot_id, in_body)
            for child in element:
                self.element(child, xpath, counters, in_body)
            if element.tail:
                self.text(element.tail, None, in_body)
            return

        counters[tag] = counters.get(tag, 0) + 1
        own_xpath = f"{xpath}/{tag}[{counters[tag]}]"
        start = self.length
        attributes = [(name, value) for name, value in element.attrib.items()
                      if not (self.web and name in self.HARNESS_ATTRIBUTES)]
        if tag == "svg":
            attributes.insert(0, ("xmlns", "http://www.w3.org/2000/svg"))
        self.write("<" + tag + "".join(f' {name}="{html_lib.escape(value)}"' for name, value in attributes) + ">")
        if tag in BLOCK_TAGS and in_body:
            self.visible.append((" ", None))
        child_counters: dict[str, int] = {}
        slot_id = int(element.get("data-t")) if element.get("data-t") is not None else None
        if slot_id is not None:
            self.slot_xpath[slot_id] = own_xpath
        if element.text:
            self.text(element.text, slot_id, in_body)
        for child in element:
            self.element(child, own_xpath, child_counters, in_body)
        if tag not in VOID_TAGS:
            self.write(f"</{tag}>")
        if tag in BLOCK_TAGS and in_body:
            self.visible.append((" ", None))
        self.element_range[id(element)] = (start, self.length, own_xpath)
        if element.tail:
            self.text(element.tail, None, in_body)

    def document(self, root) -> str:
        self.write("<!doctype html>\n")
        self.element(root, "", {}, False)
        self.write("\n")
        return "".join(self.parts)


def text_element(tag: str, text: str, **attributes) -> etree._Element:
    element = etree.Element(tag, {name.replace("_", "-"): value for name, value in attributes.items()})
    element.text = text
    return element


def page_document(title: str, style: str, body_children: list) -> etree._Element:
    root = etree.Element("html", {"lang": "en"})
    head = etree.SubElement(root, "head")
    etree.SubElement(head, "meta", {"charset": "utf-8"})
    head.append(text_element("title", title))
    head.append(text_element("style", style))
    body = etree.SubElement(root, "body")
    for child in body_children:
        body.append(child)
    return root


def running_bar(tag: str, region: str, texts: list[str]) -> etree._Element:
    bar = etree.Element(tag, {"class": "running", "data-region": region})
    for text in texts:
        bar.append(text_element("span", text))
    return bar


def navigation(nav: dict[str, list[str]]) -> etree._Element:
    element = etree.Element("nav", {"data-region": "nav"})
    outer = etree.SubElement(element, "ul")
    for group, models in nav.items():
        item = text_element("li", group)
        inner = etree.SubElement(item, "ul")
        for model in models:
            entry = etree.SubElement(inner, "li")
            entry.append(text_element("a", model, href="#" + model.lower().replace(" ", "-")))
        outer.append(item)
    return element


# --- the join and the truth -----------------------------------------------------------------------------------

def join_pdf_chars(dom_chars: list[dict], pdf_path: pathlib.Path) -> dict:
    """Pair every measured DOM character with the PDF text-layer character that drew it. Returns the self-check;
    sets `pdf_box` on each DOM character. Order-free (nearest same-text glyph), so two-column reading order cannot
    derail it."""
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        pdf_chars = [c for c in pdf.pages[0].chars if not c["text"].isspace()] if page_count else []
    problems: list[str] = []
    if page_count != 1:
        problems.append(f"the PDF has {page_count} pages, expected 1")
    # Keyed by NFKC: Chrome writes some CJK glyphs of Noto Sans CJK to the text layer as Kangxi radicals (用 as
    # U+2F64 ⽤), which NFKC folds back. CONTRACT §4 compares after NFKC too; the folds are counted, not hidden.
    by_text: dict[str, list[dict]] = {}
    for pdf_char in pdf_chars:
        by_text.setdefault(unicodedata.normalize("NFKC", pdf_char["text"]), []).append(pdf_char)
    claimed: set[int] = set()
    x_errors, y_errors = [], []
    folded: dict[str, str] = {}
    for dom_char in dom_chars:
        x0, top, x1, bottom = (value * PX_TO_PT for value in dom_char["box_px"])
        centre = (top + bottom) / 2
        options = [c for c in by_text.get(unicodedata.normalize("NFKC", dom_char["text"]), [])
                   if id(c) not in claimed]
        if not options:
            problems.append(f"DOM {dom_char['text']!r} at ({x0:.1f}, {top:.1f}) pt has no PDF character")
            continue
        best = min(options, key=lambda c: abs(c["x0"] - x0) + abs((c["top"] + c["bottom"]) / 2 - centre))
        claimed.add(id(best))
        if best["text"] != dom_char["text"]:
            folded[best["text"]] = dom_char["text"]
        x_error = abs(best["x0"] - x0)
        y_error = abs((best["top"] + best["bottom"]) / 2 - centre)
        x_errors.append(x_error)
        y_errors.append(y_error)
        if x_error >= X_TOLERANCE_PT or y_error >= Y_TOLERANCE_PT:
            problems.append(f"{dom_char['text']!r}: x error {x_error:.3f} pt, y error {y_error:.2f} pt")
        dom_char["pdf_box"] = (best["x0"], best["top"], best["x1"], best["bottom"])
    for pdf_char in pdf_chars:
        if id(pdf_char) not in claimed:
            problems.append(f"PDF {pdf_char['text']!r} at ({pdf_char['x0']:.1f}, {pdf_char['top']:.1f}) pt "
                            "was not measured on the page")
    return {"pdf_chars": len(pdf_chars), "dom_chars": len(dom_chars), "matched": len(x_errors),
            "x_error_pt_max": round(max(x_errors, default=0.0), 4),
            "x_error_pt_median": round(statistics.median(x_errors), 4) if x_errors else 0.0,
            "y_centre_offset_pt_median": round(statistics.median(y_errors), 3) if y_errors else 0.0,
            "text_layer_nfkc_folds": {pdf_text: f"U+{ord(pdf_text[0]):04X} -> {dom_text}"
                                      for pdf_text, dom_text in sorted(folded.items())},
            "problems": problems[:20], "problem_count": len(problems), "passed": not problems}


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def normalised(box, width, height) -> list[float]:
    return [round(box[0] / width, 6), round(box[1] / height, 6), round(box[2] / width, 6), round(box[3] / height, 6)]


def region_words(slots: list[Slot], slot_ids, char_location) -> list[tuple[str, str, int, int, list]]:
    """(region, text, slot id, first character index, per-character locations) of each word of the region slots: split at whitespace and at slot
    edges (a slot edge is an element edge, which a reader also sees as a break)."""
    found = []
    for slot_id in slot_ids:
        slot = slots[slot_id]
        if slot.region is None:
            continue
        current, locations, first = "", [], 0
        for index, character in enumerate(slot.text + " "):
            if character.isspace():
                if current:
                    found.append((slot.region, current, slot_id, first, locations))
                current, locations = "", []
            else:
                if not current:
                    first = index
                current += character
                locations.append(char_location(slot_id, index))
    return found


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: pathlib.Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write_bundle(arm_dir: pathlib.Path, *, source_bytes: bytes, source_name: str, media_type: str,
                 document: str, pages: list, blocks: list, words: list, dropped: list,
                 source_words: list, ladder: dict, case_id: str, arm: str, inventory: bool) -> None:
    """One truth bundle in CONTRACT.md's format, plus the harness-only truth.json.

    `inventory` False (the image arm) writes the ledger's source side as "no inventory", which CONTRACT §5 allows
    for images and which a converter given only the image must write too; truth.json keeps the full inventory
    either way, and score.py grades from that."""
    arm_dir.mkdir(parents=True, exist_ok=True)
    source_sha = sha256(source_bytes)
    (arm_dir / source_name).write_bytes(source_bytes)
    write_json(arm_dir / "source.json", {"sha256": source_sha, "bytes": len(source_bytes), "media_type": media_type,
                                         "retrievals": []})
    (arm_dir / "document.md").write_text(document, encoding="utf-8", newline="\n")
    write_json(arm_dir / "index.json", {
        "schema": "markdownify/index@0",
        "tool": {"name": "harness-truth", "version": "0", "pipeline": "truth"},
        "source": {"sha256": source_sha, "media_type": media_type},
        "markdown_sha256": sha256(document.encode("utf-8")),
        "range_units": "unicode_codepoints",
        "pages": pages, "blocks": blocks, "words": words, "dropped": dropped})

    # ledger.json and kpis.json use the field names markdownify's core writes (src/markdownify/ledger.py); CONTRACT §5–6 name
    # the quantities but not the JSON shape. The source side is the truth inventory ("harness-truth"), not
    # pdfplumber words, because the truth knows every word's fate exactly.
    fates = []
    dropped_counts: dict[str, int] = {}
    flagged_counts: dict[str, int] = {}
    for word in source_words:
        if word["fate"] == "dropped":
            dropped_counts[word["reason"]] = dropped_counts.get(word["reason"], 0) + 1
            fates.append(f"dropped:{word['reason']}")
        elif word["fate"] == "flagged":
            flagged_counts[word["kind"]] = flagged_counts.get(word["kind"], 0) + 1
            fates.append(f"flagged:{word['kind']}")
        else:
            fates.append("linked")
    linked = fates.count("linked")
    source_side = {"inventory": "harness-truth", "available": True, "words": len(source_words), "linked": linked,
                   "dropped": sum(dropped_counts.values()), "flagged": sum(flagged_counts.values()), "lost": 0,
                   "dropped_by_reason": dropped_counts, "flagged_by_kind": flagged_counts, "lost_words": [],
                   "fates": fates} if inventory else {"inventory": "none", "available": False}
    write_json(arm_dir / "ledger.json", {
        "schema": "markdownify/ledger@0",
        "source": source_side,
        "markdown": {"words": len(words), "linked": len(words), "unlinked": 0, "inexact": 0,
                     "unlinked_words": [], "inexact_words": []}})
    ladder_counts = {kind: {"seen": 0, "represented": 0}
                     for kind in ("prose", "simple_table", "complex_table", "plot", "diagram", "figure")}
    for kind, is_represented in ladder.values():
        ladder_counts[kind]["seen"] += 1
        ladder_counts[kind]["represented"] += int(is_represented)
    for counts in ladder_counts.values():
        counts["ratio"] = round(counts["represented"] / counts["seen"], 4) if counts["seen"] else None
    content_words = len(source_words) - sum(dropped_counts.values()) - sum(flagged_counts.values())
    write_json(arm_dir / "kpis.json", {
        "schema": "markdownify/kpis@0",
        "silently_lost": 0 if inventory else None, "unlinked": 0,
        "coverage": round(linked / content_words, 4) if content_words and inventory else None,
        "traceability": 1.0, "inexact_words": 0,
        "ladder": ladder_counts, "time": None})
    write_json(arm_dir / "truth.json", {
        "schema": "harness/truth@0", "case": case_id, "arm": arm,
        "source_words": source_words,
        "ladder": {block_id: kind for block_id, (kind, _) in ladder.items()}})


def render_case(case_dir: pathlib.Path, out_root: pathlib.Path, browser) -> dict:
    case_id = case_dir.name
    out_dir = out_root / case_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source = (case_dir / "source.md").read_text(encoding="utf-8")
    case = json.loads((case_dir / "case.json").read_text()) if (case_dir / "case.json").exists() else {}
    document, offset_map, positions = strip_directives(source)

    root = pandoc_tree(case_dir / "source.md")
    apply_directives(root, case)
    slots: list[Slot] = []
    wrap_slots(root, slots, source, offset_map)
    blocks = collect_blocks(root, slots, source, offset_map, positions, case)

    # Every content code point of document.md must have exactly one rendered glyph, and vice versa.
    markdown_words = mdtext.words(document)
    content_offsets = {offset for word in markdown_words for offset in range(word.start, word.end)
                       if mdtext.content_mask(document)[offset] == mdtext.CONTENT}
    rendered_offsets: dict[int, tuple[int, int]] = {}
    for slot_id, slot in enumerate(slots):
        for index, offset in enumerate(slot.offsets):
            if offset is not None:
                if offset in rendered_offsets:
                    raise CaseFailure(f"markdown offset {offset} rendered twice")
                rendered_offsets[offset] = (slot_id, index)
    if set(rendered_offsets) != content_offsets:
        missing = sorted(content_offsets - set(rendered_offsets))[:10]
        extra = sorted(set(rendered_offsets) - content_offsets)[:10]
        raise CaseFailure("the tokenizer and the rendered page disagree on what is content: "
                          f"not rendered {[document[i] for i in missing]} at {missing}; "
                          f"rendered but not content {[document[i] for i in extra]} at {extra}")

    title = next((block.element.xpath("string()") for block in blocks if block.type == "heading"), case_id)
    title = " ".join(title.split())

    # ---- print page (pdf + png arms)
    print_content = copy.deepcopy(root)
    page_children = []
    if case.get("running_header"):
        page_children.append(running_bar("header", "header", case["running_header"]))
    page_children.append(print_content)
    if case.get("running_footer"):
        page_children.append(running_bar("footer", "footer", case["running_footer"]))
    section = etree.Element("section", {"class": "page"})
    for child in page_children:
        section.append(child)
    print_tree = page_document(title, PRINT_STYLE, [section])
    for bar in section.iter("header", "footer"):
        wrap_slots(bar, slots, source, offset_map)
    print_slot_ids = [int(e.get("data-t")) for e in print_tree.iter() if e.get("data-t") is not None]
    print_html = Serializer(web=False).document(print_tree)
    (out_dir / "print.html").write_text(print_html, encoding="utf-8")

    page = browser.new_page(viewport={"width": PAGE_WIDTH_PX, "height": PAGE_HEIGHT_PX},
                            device_scale_factor=PNG_SCALE)
    try:
        page.goto((out_dir / "print.html").as_uri())
        page.evaluate("document.fonts.ready.then(() => true)")
        measured = page.evaluate(MEASURE_JS)
        pdf_bytes = page.pdf(prefer_css_page_size=True, print_background=True)
        png_bytes = page.locator("section.page").screenshot()
    finally:
        page.close()
    if measured["overflow"] > 1:
        raise CaseFailure(f"content overflows the one page by {measured['overflow']} px")
    (out_dir / "source.pdf").write_bytes(pdf_bytes)

    dom_chars = []
    for slot_id in print_slot_ids:
        record = measured["slots"].get(str(slot_id))
        slot = slots[slot_id]
        if record is None or record["text"] != slot.text:
            raise CaseFailure(f"slot {slot_id} {slot.text!r} was not measured as written")
        for index, (character, box) in enumerate(zip(slot.text, record["chars"])):
            if character.isspace():
                continue
            if box is None:
                raise CaseFailure(f"{character!r} in {slot.text!r} drew no glyph")
            dom_chars.append({"text": character, "box_px": box, "slot": slot_id, "index": index})
    selfcheck = join_pdf_chars(dom_chars, out_dir / "source.pdf")
    selfcheck["case"] = case_id
    write_json(out_dir / "selfcheck.json", selfcheck)
    if not selfcheck["passed"]:
        raise CaseFailure("self-check failed: " + "; ".join(selfcheck["problems"][:5]))
    pdf_box = {(c["slot"], c["index"]): c["pdf_box"] for c in dom_chars}

    # ---- web page (html arm)
    web_content = copy.deepcopy(root)
    web_content.tag = "main"
    web_content.attrib.pop("class", None)
    body_children = []
    if case.get("nav"):
        body_children.append(navigation(case["nav"]))
    body_children.append(web_content)
    web_tree = page_document(title, WEB_STYLE, body_children)
    for nav in web_tree.iter("nav"):
        wrap_slots(nav, slots, source, offset_map)
    web_slot_ids = [int(e.get("data-t")) for e in web_tree.iter() if e.get("data-t") is not None]
    web_elements = {e.get("data-b"): e for e in web_tree.iter() if e.get("data-b") is not None}
    web_regions = {e.get("data-region"): e for e in web_tree.iter() if e.get("data-region") is not None}
    web = Serializer(web=True)
    web_html = web.document(web_tree)

    # The web page's visible text, whitespace collapsed, for text_quote prefixes and suffixes.
    visible_text, visible_index = [], {}
    for character, html_offset in web.visible:
        if character.isspace():
            if visible_text and visible_text[-1] != " ":
                visible_text.append(" ")
            continue
        if html_offset is not None:
            visible_index[html_offset] = len(visible_text)
        visible_text.append(character)
    visible_string = "".join(visible_text)

    def web_grounding(start: int, end: int, xpath: str, exact: str | None) -> dict:
        grounding = {"source_range": {"start": start, "end": end}, "xpath": xpath}
        if exact is not None:
            first, last = visible_index[start], visible_index[end - 1]
            grounding["text_quote"] = {"exact": exact, "prefix": visible_string[max(0, first - 32):first].lstrip(),
                                       "suffix": visible_string[last + 1:last + 33].rstrip()}
        return {"html": grounding}

    # ---- words, blocks, inventory for each arm
    mask = mdtext.content_mask(document)

    def word_chars(word):
        return [rendered_offsets[offset] for offset in range(word.start, word.end) if mask[offset] == mdtext.CONTENT]

    page_pt = [{"page": 1, "width": round(PAGE_WIDTH_PT, 2), "height": round(PAGE_HEIGHT_PT, 2), "unit": "pt"}]
    page_px = [{"page": 1, "width": PAGE_WIDTH_PX * PNG_SCALE, "height": PAGE_HEIGHT_PX * PNG_SCALE, "unit": "px"}]

    def pdf_word_box(word):
        boxes = [pdf_box[location] for location in word_chars(word)]
        tops = sorted(box[1] for box in boxes)
        if tops[-1] - tops[0] > 4:
            # A contract word has one box; a hyphenated word broken over two lines has none that fits.
            raise CaseFailure(f"the word {word.text!r} wraps across lines; reword the case")
        return normalised(union(boxes), PAGE_WIDTH_PT, PAGE_HEIGHT_PT)

    page_words = [{"range": {"start": word.start, "end": word.end},
                   "grounding": {"page": 1, "box": pdf_word_box(word)}, "score": 1.0}
                  for word in markdown_words]

    web_problems = []

    def web_word(word):
        locations = word_chars(word)
        start = web.char_offsets[locations[0][0]][locations[0][1]]
        last_slot, last_index = locations[-1]
        end = web.char_offsets[last_slot][last_index] + len(html_lib.escape(slots[last_slot].text[last_index],
                                                                           quote=False))
        if html_lib.unescape(web_html[start:end]) != word.text:
            web_problems.append(f"html[{start}:{end}] = {web_html[start:end]!r}, word {word.text!r}")
        return web_grounding(start, end, web.slot_xpath[locations[0][0]], word.text)

    html_words = [{"range": {"start": word.start, "end": word.end}, "grounding": web_word(word), "score": 1.0}
                  for word in markdown_words]
    if web_problems:
        raise CaseFailure("web arm self-check failed: " + "; ".join(web_problems[:5]))

    def block_rows(arm: str) -> list[dict]:
        rows = []
        for index, block in enumerate(blocks):
            if arm == "html":
                start, end, xpath = web.element_range[id(web_elements[str(index)])]
                grounding = [web_grounding(start, end, xpath, None)]
            else:
                grounding = [{"page": 1, "box": normalised(measured["boxes"][f"b{index}"],
                                                           PAGE_WIDTH_PX, PAGE_HEIGHT_PX)}]
            row = {"id": block.extra["id"], "type": block.type,
                   "range": {"start": block.range[0], "end": block.range[1]}, "grounding": grounding,
                   "represented": block.extra.get("represented", True)}
            if block.type == "table":
                row["children"] = block.extra["children"]
            if block.type == "table_cell":
                row.update({name: block.extra[name] for name in ("row", "col", "rowspan", "colspan", "kind")})
            rows.append(row)
        return rows

    # Source words are page words: markdown words drawn touching each other ("torque" + superscript "1") are one
    # word to a reader and to a text-layer inventory, so they are one inventory row (linked to both).
    def dom_box(location):
        slot_id, index = location
        return measured["slots"][str(slot_id)]["chars"][index]

    page_word_groups: list[list[int]] = []
    for index, word in enumerate(markdown_words):
        if page_word_groups:
            left = dom_box(word_chars(markdown_words[page_word_groups[-1][-1]])[-1])
            right = dom_box(word_chars(word)[0])
            shared_height = min(left[3], right[3]) - max(left[1], right[1])
            if shared_height > 0.3 * min(left[3] - left[1], right[3] - right[1]) and -0.5 <= right[0] - left[2] < 1.0:
                page_word_groups[-1].append(index)
                continue
        page_word_groups.append([index])

    ladder = {}
    for block in blocks:
        if block.type in ("heading", "paragraph", "list_item", "footnote", "caption"):
            ladder[block.extra["id"]] = ("prose", True)
        elif block.type == "table":
            ladder[block.extra["id"]] = (block.extra["ladder"], True)
        elif block.type in ("plot", "diagram"):
            ladder[block.extra["id"]] = (block.type, False)

    for arm in ("pdf", "png", "html"):
        if arm == "html":
            words, slot_ids = html_words, web_slot_ids
            regions = web_regions
            pages = []

            def location(slot_id, index):
                return web.char_offsets[slot_id][index]

            def region_grounding(region):
                start, end, xpath = web.element_range[id(regions[region])]
                return web_grounding(start, end, xpath, None)
        else:
            words, slot_ids = page_words, print_slot_ids
            pages = page_pt if arm == "pdf" else page_px

            def location(slot_id, index):
                return pdf_box[(slot_id, index)]

            def region_grounding(region):
                return {"page": 1, "box": normalised(measured["boxes"]["r" + region], PAGE_WIDTH_PX, PAGE_HEIGHT_PX)}

        # Source words in page order (the order of the text in the page's DOM), as an inventory reads them.
        slot_order = {slot_id: position for position, slot_id in enumerate(slot_ids)}
        ordered = []
        for group in page_word_groups:
            text = "".join(markdown_words[index].text for index in group)
            first, last = words[group[0]]["grounding"], words[group[-1]]["grounding"]
            if arm == "html":
                grounding = web_grounding(first["html"]["source_range"]["start"], last["html"]["source_range"]["end"],
                                          first["html"]["xpath"], text)
            else:
                boxes = [words[index]["grounding"]["box"] for index in group]
                grounding = {"page": 1, "box": [round(value, 6) for value in union(boxes)]}
            first_char = word_chars(markdown_words[group[0]])[0]
            ordered.append(((slot_order[first_char[0]], first_char[1]),
                            {"text": text, "grounding": grounding, "fate": "linked", "words": group}))
        for region, text, slot_id, first, locations in region_words(slots, slot_ids, location):
            if arm == "html":
                grounding = web_grounding(locations[0], locations[-1] + 1, web.slot_xpath[slot_id], text)
            else:
                grounding = {"page": 1, "box": normalised(union(locations), PAGE_WIDTH_PT, PAGE_HEIGHT_PT)}
            row = {"text": text, "grounding": grounding}
            if region in DROP_REASONS:
                row.update(fate="dropped", reason=DROP_REASONS[region])
            else:
                row.update(fate="flagged", kind=FLAG_KINDS[region])
            ordered.append(((slot_order[slot_id], first), row))
        source_words = [row for _, row in sorted(ordered, key=lambda pair: pair[0])]
        present_regions = {slots[s].region for s in slot_ids if slots[s].region}
        dropped = [{"grounding": region_grounding(region), "reason": DROP_REASONS[region]}
                   for region in DROP_REASONS if region in present_regions]

        if arm == "pdf":
            source_bytes, source_name, media_type = pdf_bytes, "source.pdf", "application/pdf"
        elif arm == "png":
            source_bytes, source_name, media_type = png_bytes, "source.png", "image/png"
        else:
            source_bytes, source_name, media_type = web_html.encode("utf-8"), "source.html", "text/html"
        write_bundle(out_dir / arm, source_bytes=source_bytes, source_name=source_name, media_type=media_type,
                     document=document, pages=pages, blocks=block_rows(arm), words=words, dropped=dropped,
                     source_words=source_words, ladder=ladder, case_id=case_id, arm=arm,
                     inventory=arm != "png")
    (out_dir / "source.pdf").unlink()
    return selfcheck


def case_dirs(names: list[str], all_cases: bool) -> list[pathlib.Path]:
    if all_cases:
        return sorted(path for path in CASES_DIR.iterdir() if (path / "source.md").exists())
    found = []
    for name in names:
        matches = [path for path in CASES_DIR.iterdir() if path.name == name or path.name.split("-", 1)[0] == name]
        if not matches:
            raise SystemExit(f"no case {name!r} under {CASES_DIR}")
        found.extend(matches)
    return found


def render(names: list[str], all_cases: bool = False, out_root: pathlib.Path = OUT_DIR) -> dict[str, dict]:
    from playwright.sync_api import sync_playwright

    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for case_dir in case_dirs(names, all_cases):
                try:
                    results[case_dir.name] = render_case(case_dir, out_root, browser)
                except CaseFailure as failure:
                    results[case_dir.name] = {"passed": False, "error": str(failure)}
        finally:
            browser.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cases", nargs="*", help="case directory names (or their number prefixes)")
    parser.add_argument("--all", action="store_true", help="render every case under harness/cases")
    parser.add_argument("--out", type=pathlib.Path, default=OUT_DIR, help="output root (default harness/out)")
    arguments = parser.parse_args(argv)
    if not arguments.cases and not arguments.all:
        parser.error("name a case or pass --all")
    results = render(arguments.cases, arguments.all, arguments.out)
    print(f"{'case':<24} {'self-check':<10} {'chars':>6} {'x err max pt':>13} {'y offset pt':>12}")
    for case_id, result in results.items():
        if result.get("passed"):
            print(f"{case_id:<24} {'PASS':<10} {result['matched']:>6} {result['x_error_pt_max']:>13.4f} "
                  f"{result['y_centre_offset_pt_median']:>12.3f}")
        else:
            print(f"{case_id:<24} {'FAIL':<10} {result.get('error', '')}")
    return 0 if all(result.get("passed") for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
