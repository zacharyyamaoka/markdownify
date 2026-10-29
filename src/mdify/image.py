"""PNG/JPEG of a digital page → markdown, through docling's OCR path (pipeline A, OCR first, then align).

- **OCR:** RapidOCR (PP-OCR models on onnxruntime), which docling ships an adapter for and which runs here without a
  system install; full-page OCR, because an image has no text layer to prefer.
- **Structure:** docling's layout model and TableFormer, the same as for a PDF, so the same serializer
  (`mdify.docling_markdown`) writes the markdown.
- **Align:** markdown words are aligned to the OCR lines; a word's box is its slice of the line's box, in
  proportion to its characters (OCR gives line boxes, not character boxes).

Inventory: none in v0 (CONTRACT §5) — the only words an image has are the OCR's own, and grading the OCR against
itself would prove nothing. Ground truth for synthetic pages belongs to the harness. `text_under` likewise has no
independent text to return, so `mdify check` reports invariant 1 as not checkable for images.
"""

from __future__ import annotations

import functools

from .converters import Conversion, Source, SourceWord

MEDIA_TYPES = ("image/png", "image/jpeg")
PIPELINE = "A"


@functools.lru_cache(maxsize=1)
def _converter():
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    options = PdfPipelineOptions(generate_parsed_pages=True)
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
    options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.AUTO)
    converter = DocumentConverter(format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.IMAGE)
    return converter


def warm_up() -> None:
    _converter()


def ocr_units(result) -> list:
    """docling's OCR cells (one per detected line) as alignment units, boxes 0–1 of the page, origin top-left.
    `Page.page_no` is 1-based, like the document's pages."""
    from .docling_markdown import SourceText

    units = []
    for page in result.pages:
        width, height = page.size.width, page.size.height
        for cell in page.cells:
            text = cell.text or ""
            if not text.strip():
                continue
            box = cell.rect.to_bounding_box().to_top_left_origin(page_height=height)
            units.append(SourceText(text=text, raw=text, page=page.page_no,
                                    box=(box.l / width, box.t / height, box.r / width, box.b / height)))
    return units


def convert(source: Source) -> Conversion:
    from PIL import Image

    from . import docling_markdown

    result = _converter().convert(str(source.path))
    with Image.open(source.path) as picture:
        width, height = picture.size
    pages = [{"page": 1, "width": width, "height": height, "unit": "px"}]
    return docling_markdown.serialize(result.document, ocr_units(result), pages=pages, folded_note="ocr")


def inventory(source: Source) -> list[SourceWord] | None:
    """No inventory for a real image in v0 (CONTRACT §5)."""
    return None


def text_under(source: Source, grounding: dict) -> str | None:
    """No text layer: nothing independent of the OCR to compare a word with."""
    return None
