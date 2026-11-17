"""Pipeline B's first step: a source → one PNG per page, the only thing the model sees.

- **PDF:** pdfium (pypdfium2, which pdfplumber already depends on) at a fixed 192 DPI. WHY 192: a Letter page comes
  out 1632 × 2112 px — the same size as the harness's image arm (a 2× screenshot of the 816 × 1056 CSS px page), so
  the pdf and png arms show the model the same pixels — and under the 2576 px long edge Opus 5.x reads at full
  resolution, so the pixel coordinates it gives map 1:1 onto the image we sent.
- **PNG/JPEG:** as is, unless the long edge is over 2576 px; then scaled down to it (boxes are normalised to 0–1 of
  the image either way, so a scaled image only costs resolution).
- **HTML:** a full-page Playwright screenshot at a 1280 px wide viewport, treated as one page (the harness extra
  installs Playwright; imported only here). Its boxes are 0–1 of the screenshot, not ranges into the HTML. A page
  taller than 2576 px is scaled down whole, so a long page loses legibility — not split into tiles in v0.
"""

from __future__ import annotations

import dataclasses
import pathlib

from .converters import Source

PDF_DPI = 192
MAX_LONG_EDGE = 2576            # Opus 5.x's full-resolution limit; larger images are downscaled by the API anyway
HTML_VIEWPORT_WIDTH = 1280


@dataclasses.dataclass(frozen=True)
class PageImage:
    page: int                   # 1-based
    path: pathlib.Path          # the PNG the model is shown
    width_px: int               # its size, stated to the model
    height_px: int
    size: dict                  # the index's page entry: {"page", "width", "height", "unit"}


def _fit(picture):
    """`picture` scaled down so its long edge is at most MAX_LONG_EDGE (never up)."""
    long_edge = max(picture.size)
    if long_edge <= MAX_LONG_EDGE:
        return picture
    scale = MAX_LONG_EDGE / long_edge
    return picture.resize((round(picture.width * scale), round(picture.height * scale)))


def _save(picture, page: int, directory: pathlib.Path, size: dict) -> PageImage:
    picture = _fit(picture.convert("RGB"))
    path = directory / f"page-{page}.png"
    picture.save(path)
    return PageImage(page=page, path=path, width_px=picture.width, height_px=picture.height, size=size)


def render(source: Source, directory: pathlib.Path) -> list[PageImage]:
    directory.mkdir(parents=True, exist_ok=True)
    if source.media_type == "application/pdf":
        import pypdfium2

        pages = []
        document = pypdfium2.PdfDocument(str(source.path))
        try:
            for number in range(len(document)):
                page = document[number]
                width, height = page.get_size()
                picture = page.render(scale=PDF_DPI / 72).to_pil()
                pages.append(_save(picture, number + 1, directory, {"page": number + 1, "width": round(width, 2),
                                                                   "height": round(height, 2), "unit": "pt"}))
                page.close()
        finally:
            document.close()
        return pages
    if source.media_type in ("image/png", "image/jpeg"):
        from PIL import Image

        with Image.open(source.path) as picture:
            picture.load()
            return [_save(picture, 1, directory, {"page": 1, "width": picture.width, "height": picture.height,
                                                  "unit": "px"})]
    if source.media_type in ("text/html", "application/xhtml+xml"):
        from PIL import Image
        from playwright.sync_api import sync_playwright

        shot = directory / "screenshot.png"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": HTML_VIEWPORT_WIDTH, "height": 800})
                page.goto(source.path.resolve().as_uri(), wait_until="load")
                page.screenshot(path=str(shot), full_page=True)
            finally:
                browser.close()
        with Image.open(shot) as picture:
            picture.load()
            return [_save(picture, 1, directory, {"page": 1, "width": picture.width, "height": picture.height,
                                                  "unit": "px"})]
    raise ValueError(f"pipeline B cannot render {source.media_type}")
