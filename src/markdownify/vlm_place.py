"""Pipeline AB (`python -m markdownify.vlm … --place words`): the model writes the markdown and its line boxes; the source's
own words place each markdown word inside its line.

WHY (measured 2026-10-29, scripts/pipeline_b_diagnose.py): every one of pipeline B's misplaced words — 125 of 1842
for Opus 5.5, 136 for GPT-6.1 Sol on the harness — sat inside a line box the model had drawn correctly. The error was
ours: a line box split by character count drifts on short words, superscripts and double-width CJK glyphs. The model
is good at *which line*; the source is exact at *where in the line*. So the line box stops being the answer and
becomes the search window:

- **Source words:** a PDF's text layer (pdfplumber words with their character boxes, as pipeline A reads them); a
  page image, an image-only PDF or an HTML screenshot has none, so RapidOCR (the engine docling ships, already
  installed) reads word boxes off the same page image the model saw (`return_word_box=True`).
- **Window:** each line's (or cell's) markdown words are aligned (`markdownify.align.align_word_spans`, the same aligner
  as pipeline A) to the source words that overlap its box; words still unplaced then try the source words of their
  whole block (a line box the model drew a little short). No page-wide pass: a word never finds a twin elsewhere.
- **Box:** the union of the characters it matched (`docling_markdown.box_of_characters`: exact character boxes from
  a text layer, an even split of the OCR word's box otherwise).
- **Unplaced:** keeps its character-count slice of the model's line box. Its score is then whatever the text layer
  says under that box (a PDF) or `null` with note "unverified" (no text layer) — never 1.0 by default.
- **Score of a word placed on OCR:** the aligner's score against the OCR word, with note "ocr": OCR is an engine
  independent of the model, but not the source itself.
"""

from __future__ import annotations

import bisect
import functools
import pathlib

from . import align
from .docling_markdown import SourceText, box_of_characters, rounded, union

# How far around a model's line box a source word may reach and still count as in it: a fraction of the line's
# height vertically, of the page horizontally. WHY: the model's boxes are good but not pixel-tight (harness median
# line IoU ≈ 0.7 against text-layer boxes, which are taller than the ink).
_VERTICAL_SLACK = 0.35
_HORIZONTAL_SLACK = 0.004
_OVERLAP_SHARE = 0.5          # a source word belongs to a window when half its area lies inside it


@functools.lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def ocr_units(page: int, image: pathlib.Path) -> list[SourceText]:
    """RapidOCR's word boxes on one page image, 0–1 of the image. A line without word boxes is one unit."""
    from PIL import Image

    with Image.open(image) as picture:
        width, height = picture.size
    result = _ocr_engine()(str(image), return_word_box=True)
    units = []

    def unit(text: str, polygon) -> SourceText:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return SourceText(text=text, raw=text, page=page,
                          box=(min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height))

    lines = list(zip(result.txts or (), result.boxes if result.boxes is not None else ()))
    words = result.word_results or ()
    for number, (text, polygon) in enumerate(lines):
        line_words = words[number] if number < len(words) else ()
        if line_words:
            units.extend(unit(word_text, word_polygon) for word_text, _score, word_polygon in line_words
                         if word_text.strip())
        elif text.strip():
            units.append(unit(text, polygon))
    return units


def source_units(source, images) -> tuple[list[SourceText], str]:
    """(units, "text_layer" | "ocr"): the words that place the markdown's words."""
    if source.media_type == "application/pdf":
        from . import pdf

        if pdf.has_text_layer(source.path):
            units = [SourceText(text=pdf.decode_symbol_font(word["text"]), raw=word["text"], page=layer["page"],
                                box=word["box"], char_boxes=word["char_boxes"])
                     for layer in pdf.text_layer(source.path) for word in layer["words"]]
            return units, "text_layer"
    return [unit for image in images for unit in ocr_units(image.page, image.path)], "ocr"


def _window(box: list[float]) -> tuple[float, float, float, float]:
    slack = (box[3] - box[1]) * _VERTICAL_SLACK
    return (box[0] - _HORIZONTAL_SLACK, box[1] - slack, box[2] + _HORIZONTAL_SLACK, box[3] + slack)


def _inside(unit: SourceText, window: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = unit.box
    width = max(min(x1, window[2]) - max(x0, window[0]), 0.0)
    height = max(min(y1, window[3]) - max(y0, window[1]), 0.0)
    area = max(x1 - x0, 1e-6) * max(y1 - y0, 1e-6)
    return width * height >= _OVERLAP_SHARE * area


def place(markdown: str, rows: list[dict], segments: list, units: list[SourceText], kind: str) -> dict:
    """Move each word row onto the source characters it matches inside its segment's window. Returns counts."""
    words = align.markdown_words(markdown)
    ordered = sorted((segment for segment in segments if segment.end > segment.start and segment.box),
                     key=lambda segment: segment.start)
    starts = [segment.start for segment in ordered]
    by_page: dict[int, list[int]] = {}
    for j, unit in enumerate(units):
        by_page.setdefault(unit.page, []).append(j)

    segment_words: dict[int, list[int]] = {}
    for index, word in enumerate(words):
        position = bisect.bisect_right(starts, word.start) - 1
        if position >= 0 and word.start < ordered[position].end:
            segment_words.setdefault(position, []).append(index)

    def candidates(segment) -> list[int]:
        window = _window(segment.box)
        return [j for j in by_page.get(segment.page, []) if _inside(units[j], window)]

    line_groups, block_groups = [], {}
    for position, segment in enumerate(ordered):
        indices = segment_words.get(position, [])
        if not indices:
            continue
        found = candidates(segment)
        line_groups.append((indices, found))
        group = block_groups.setdefault((segment.page, segment.block), ([], []))
        group[0].extend(indices)
        group[1].extend(found)
    groups = line_groups + [(indices, sorted(set(found))) for indices, found in block_groups.values()]
    alignments = align.align_word_spans([word.text for word in words], [unit.text for unit in units],
                                        groups=groups, residual=False)

    placed = 0
    for row, alignment in zip(rows, alignments):
        if not alignment.sources:
            continue
        page = units[alignment.sources[0][0]].page
        boxes = [box_of_characters(units[j], start, end) for j, start, end in alignment.sources
                 if units[j].page == page]
        row["grounding"] = {"page": page, "box": rounded(union(boxes))}
        row["placed"] = kind
        row["score"] = alignment.score
        placed += 1
    return {"placed": placed, "words": len(rows), "source_words": len(units), "source": kind}
