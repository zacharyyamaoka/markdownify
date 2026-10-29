"""Born-digital PDF → markdown, pipeline A ("parse first, then align").

- **Parse:** docling's `DocumentConverter` (standard PDF pipeline: layout model + TableFormer, on the GPU) reads the
  structure — headings, paragraphs, lists, tables with their cells and header flags, pictures, page furniture.
  OCR is off: a born-digital PDF's text layer is the text, and OCR would only add guesses from pictures.
- **Serialise:** `mdify.docling_markdown` writes the markdown item by item, recording each block's and cell's range.
- **Align:** each markdown word is aligned (`mdify.align`) to pdfplumber's words of the page, block by block, and
  grounded on the boxes of exactly the characters it matched — so a word docling split or joined still gets a box
  around its own characters.

The ledger's inventory is pdfplumber's word list, and `text_under` reads pdfplumber's characters: both read the PDF
without docling (CONTRACT §5), so `mdify check` never asks the converter to grade itself, and needs no model.

A PDF with no text layer at all (an image-only "datasheet", like Deep Robotics' J60 sheet) is out of v0's scope
(CONTRACT §1); it is still converted — through docling's OCR, like a page image — so its tables are not silently
empty, but it has no inventory and its words cannot be checked against a text layer.

Symbol-font text: some makers' PDFs (Mean Well's) draw letters with a symbol font whose text layer holds private-use
code points (U+F041 for "A"). Both sides of the alignment decode them (`decode_symbol_font`), the markdown shows the
letters, and every word that needed it is scored 0.99 with `note: "symbol_font"` — flagged, never hidden.
"""

from __future__ import annotations

import functools
import os
import pathlib
import statistics

import pdfplumber

from .converters import Conversion, Source, SourceWord

MEDIA_TYPES = ("application/pdf",)
PIPELINE = "A"


def decode_symbol_font(text: str) -> str:
    """U+F020–U+F0FF → U+0020–U+00FF: the private-use range a symbol font without a ToUnicode map writes for the
    Latin-1 glyphs it draws. One code point for one, so character offsets are unchanged."""
    return "".join(chr(ord(character) - 0xF000) if 0xF020 <= ord(character) <= 0xF0FF else character
                   for character in text)


# --- pdfplumber: the text layer ---------------------------------------------------------------------------------
@functools.lru_cache(maxsize=8)
def _text_layer(path: str, _mtime: float) -> list[dict]:
    """Per page: size, deduplicated characters and words (each word with the boxes of its characters), all
    normalised to 0–1 of the page, origin top-left. WHY dedupe: fake-bold PDFs draw each glyph twice."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            x_origin, y_origin = page.bbox[0], page.bbox[1]
            width, height = float(page.width), float(page.height)

            def box(item) -> tuple[float, float, float, float]:
                return ((float(item["x0"]) - x_origin) / width, (float(item["top"]) - y_origin) / height,
                        (float(item["x1"]) - x_origin) / width, (float(item["bottom"]) - y_origin) / height)

            deduplicated = page.dedupe_chars()
            characters = [{"text": char["text"], "box": box(char)} for char in deduplicated.chars]
            words = []
            for word in deduplicated.extract_words(return_chars=True):
                char_boxes = []
                for char in word["chars"]:
                    char_boxes.extend([box(char)] * len(char["text"]))
                if len(char_boxes) != len(word["text"]):  # never seen; keep the word, interpolate its characters
                    char_boxes = None
                words.append({"text": word["text"], "box": box(word), "char_boxes": char_boxes})
            pages.append({"page": page.page_number, "width": width, "height": height,
                          "characters": characters, "words": words})
    return pages


def text_layer(path: pathlib.Path) -> list[dict]:
    return _text_layer(str(path), os.path.getmtime(path))


def has_text_layer(path: pathlib.Path) -> bool:
    return any(page["characters"] for page in text_layer(path))


# --- docling: the structure -------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=2)
def _converter(ocr: bool):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(generate_parsed_pages=True)
    options.do_ocr = ocr
    if ocr:
        options.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
    options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.AUTO)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def warm_up() -> None:
    """Load docling's models (the core times this apart from the conversion: cold vs warm, CONTRACT §6)."""
    _converter(False)


def convert(source: Source) -> Conversion:
    from . import docling_markdown
    from .docling_markdown import SourceText

    if not has_text_layer(source.path):
        from .image import ocr_units
        result = _converter(True).convert(str(source.path))
        return docling_markdown.serialize(result.document, ocr_units(result), folded_note="ocr")

    result = _converter(False).convert(str(source.path))
    units = []
    for page in text_layer(source.path):
        for word in page["words"]:
            units.append(SourceText(text=decode_symbol_font(word["text"]), raw=word["text"], page=page["page"],
                                    box=word["box"], char_boxes=word["char_boxes"]))
    pages = [{"page": page["page"], "width": round(page["width"], 2), "height": round(page["height"], 2), "unit": "pt"}
             for page in text_layer(source.path)]
    conversion = docling_markdown.serialize(result.document, units, fold=decode_symbol_font, pages=pages)
    _score_against_text_layer(conversion, source)
    return conversion


def _score_against_text_layer(conversion: Conversion, source: Source) -> None:
    """Re-score every linked word against the text-layer characters under its final box, exactly as `mdify check`
    reads them (CONTRACT §3.2: the score is the word against the source text under its box).

    WHY after alignment: the aligner scores a word against the source word it matched, but its box can also take in
    another word's glyphs — makers' templates stack two products' names on the same spot ("RSP-500" over "USP-225")
    — and then the box does not say what the word says. Such a word stays linked, scored below 1.0 and listed."""
    from . import align
    from rapidfuzz.distance import Levenshtein

    mask = align.content_mask(conversion.markdown)
    for word in conversion.words:
        if not word.get("grounding"):
            continue
        under = text_under(source, word["grounding"])
        if under is None:
            continue
        text = align.normalize(align.word_text(conversion.markdown, word["range"]["start"], word["range"]["end"], mask))
        decoded = align.normalize(decode_symbol_font(under))
        score = round(Levenshtein.normalized_similarity(text, decoded), 4)
        if decoded != align.normalize(under):
            score = min(score, 0.99)
            word["note"] = "symbol_font"
        elif word.get("note") == "symbol_font":
            del word["note"]
        word["score"] = score


# --- the independent side: inventory and text_under -------------------------------------------------------------
def inventory(source: Source) -> list[SourceWord] | None:
    """pdfplumber's words, read without docling. An image-only PDF has no text layer, so "no inventory" (None),
    as for an image (CONTRACT §5) — not an empty one, which would read as "nothing to cover"."""
    if not has_text_layer(source.path):
        return None
    return [SourceWord(text=word["text"], grounding={"page": page["page"], "box": [round(v, 5) for v in word["box"]]})
            for page in text_layer(source.path) for word in page["words"]]


def text_under(source: Source, grounding: dict) -> str | None:
    """The text-layer characters whose centre lies inside the box, left to right (top to bottom first when the
    box holds more than one line). None when the page has no text layer: nothing independent to compare with."""
    pages = text_layer(source.path)
    page_no = grounding.get("page")
    if not page_no or page_no > len(pages):
        return ""
    page = pages[page_no - 1]
    if not page["characters"]:
        return None
    x0, y0, x1, y1 = grounding["box"]
    epsilon = 1e-4
    inside = []
    for character in page["characters"]:
        cx0, cy0, cx1, cy1 = character["box"]
        center_x, center_y = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        if x0 - epsilon <= center_x <= x1 + epsilon and y0 - epsilon <= center_y <= y1 + epsilon:
            inside.append(character)
    if not inside:
        return ""
    heights = [c["box"][3] - c["box"][1] for c in inside]
    line_height = statistics.median(heights) or 1e-3
    if (y1 - y0) <= 1.6 * line_height:
        inside.sort(key=lambda c: c["box"][0])
    else:
        inside.sort(key=lambda c: (round(((c["box"][1] + c["box"][3]) / 2) / line_height), c["box"][0]))
    return "".join(c["text"] for c in inside)
