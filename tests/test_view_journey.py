"""`markdownify view` in headless Chromium on real bundles. Every check is a measurement — getBoundingClientRect against a
rectangle computed here from the bundle's own index.json (or the browser's own Range, for a web page) — never "the
element exists". Skips when Chromium or the bundles are not on this machine.

    uv run pytest tests/test_view_journey.py -s          # the measured journey
    uv run python tests/test_view_journey.py --media     # + the hero video and screenshots in reports/media/view/

The bundles are Markdownify's cache entries (CONTRACT §2), made with `markdownify <source>`: the Mean Well LRS-350 and TI
TPS54331 datasheets, and the CubeMars AK80-9 product page (source-highlighter's demo/assets/cubemars-ak80-9.html).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

REPO = pathlib.Path(__file__).resolve().parents[1]
MEDIA = REPO / "reports" / "media" / "view"
BUNDLES = pathlib.Path(os.environ.get("MARKDOWNIFY_CACHE") or pathlib.Path.home() / ".cache" / "markdownify") / "bundles"
LRS = BUNDLES / "ac3e7b8d27dcd72277b6bdbb208586a2343a635edf1ff578fffd131a31713f6f"
TPS = BUNDLES / "cf72dfd0ac69eec645b7b493de628dc1c3aa66f5f925a2ea9be6bb5c38260730"
CUBEMARS = BUNDLES / "04e9b6e63a10d733533f0677b8daa6c8b4f490573a233e4eaae6259e1d5b11f2"
PADDING = 2  # the highlighter's default padding for every style (its STYLE_DEFAULTS)
TOLERANCE = 1
VIEWPORT = {"width": 1500, "height": 940}
RESULTS: dict[str, object] = {}


def _have(bundle: pathlib.Path) -> bool:
    return (bundle / "index.json").is_file()


# ---- the real command, stopped by its own pid ------------------------------------------------------------------

class Viewer:
    def __init__(self, bundle: pathlib.Path, port: int = 0):
        markdownify = pathlib.Path(sys.executable).with_name("markdownify")
        self.process = subprocess.Popen([str(markdownify), "view", str(bundle), "--port", str(port)], stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        lines = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if line.strip().startswith("http://127.0.0.1:"):
                self.url = line.strip()
                return
        self.stop()
        raise RuntimeError(f"markdownify view did not print its URL: {''.join(lines)}")

    def stop(self) -> None:
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 - no browser on this machine is a skip, not a failure
            pytest.skip(f"no Chromium for Playwright: {error}")
        yield launched
        launched.close()


def open_view(browser, bundle: pathlib.Path):
    if not _have(bundle):
        pytest.skip(f"bundle not in the cache: {bundle}")
    viewer = Viewer(bundle)
    context = browser.new_context(viewport=VIEWPORT)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(viewer.url)
    wait_ready(page)
    return viewer, context, page, errors


def wait_ready(page) -> None:
    page.wait_for_selector('[data-testid="source-pane"][data-state="ready"]', timeout=120_000)
    page.wait_for_timeout(300)


def load(bundle: pathlib.Path):
    index = json.loads((bundle / "index.json").read_text(encoding="utf-8"))
    ledger = json.loads((bundle / "ledger.json").read_text(encoding="utf-8"))
    kpis = json.loads((bundle / "kpis.json").read_text(encoding="utf-8"))
    markdown = (bundle / "document.md").read_bytes().decode("utf-8")
    return index, ledger, kpis, markdown


# ---- measuring -----------------------------------------------------------------------------------------------------

def near(actual: dict, expected: dict, what: str) -> dict:
    for key in ("left", "top", "right", "bottom"):
        assert abs(actual[key] - expected[key]) <= TOLERANCE, f"{what}: {key} {actual[key]:.2f} vs expected {expected[key]:.2f}"
    return {"measured": rounded(actual), "expected": rounded(expected)}


def rounded(rect: dict) -> dict:
    return {key: round(rect[key], 2) for key in ("left", "top", "right", "bottom")}


def expected_box_rect(page, location: dict, page_size: dict) -> dict:
    """Where a page/box Location lands on the rendered page: the highlighter's geometry rule (its README "Geometry"),
    with the page size from index.json's own pdfplumber measurement — not from the highlighter."""
    return page.evaluate("""([location, size, padding]) => {
      const rect = document.querySelector(`[data-testid="source-pane"] .sh-page[data-page="${location.page ?? 1}"]`).getBoundingClientRect()
      const zoom = rect.width / size.width
      const [x0, y0, x1, y1] = location.box
      const clip = (value, max) => Math.min(Math.max(value, 0), max)
      return {
        left: rect.left + clip(x0 * size.width - padding, size.width) * zoom,
        top: rect.top + clip(y0 * size.height - padding, size.height) * zoom,
        right: rect.left + clip(x1 * size.width + padding, size.width) * zoom,
        bottom: rect.top + clip(y1 * size.height + padding, size.height) * zoom,
      }
    }""", [location, page_size, PADDING])


def mark_rect(page, mark_id: str = "sel-0") -> dict | None:
    return page.evaluate("""(id) => {
      const mark = document.querySelector(`[data-testid="source-pane"] .sh-mark[data-id="${id}"]`)
      if (!mark) return null
      const { left, top, right, bottom } = mark.getBoundingClientRect()
      return { left, top, right, bottom }
    }""", mark_id)


def visible_in(page, selector: str, rect: dict) -> bool:
    return page.evaluate("""([selector, rect]) => {
      const box = document.querySelector(selector).getBoundingClientRect()
      return rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1 && rect.left >= box.left - 1 && rect.right <= box.right + 1
    }""", [selector, rect])


def width_of(page, test_id: str) -> float:
    return page.evaluate("""(id) => {
      const node = document.querySelector(`[data-testid="${id}"]`)
      return node && !node.hidden ? node.getBoundingClientRect().width : 0
    }""", test_id)


def drag(page, test_id: str, dx: float) -> None:
    handle = page.locator(f'[data-testid="{test_id}"]').bounding_box()
    x = handle["x"] + handle["width"] / 2
    y = handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x + dx * step / 10, y)
    page.mouse.up()
    page.wait_for_timeout(300)


def word_in_cell(index: dict, cell: dict) -> int:
    return next(at for at, word in enumerate(index["words"])
                if word["range"]["start"] >= cell["range"]["start"] and word["range"]["end"] <= cell["range"]["end"])


def lrs_cells(index: dict, markdown: str) -> tuple[dict, dict]:
    """The "LRS-350-24" header cell and the RATED POWER value under it ("350.4W"), found by their text and column."""
    cells = [block for block in index["blocks"] if block["type"] == "table_cell"]
    text = lambda block: markdown[block["range"]["start"]:block["range"]["end"]]  # noqa: E731
    header = next(block for block in cells if text(block) == "LRS-350-24")
    value = next(block for block in cells if text(block) == "350.4W" and block["col"] == header["col"])
    return header, value


# ---- the Mean Well LRS-350 -----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lrs(browser):
    viewer, context, page, errors = open_view(browser, LRS)
    index, ledger, kpis, markdown = load(LRS)
    yield {"page": page, "index": index, "kpis": kpis, "markdown": markdown, "errors": errors, "viewer": viewer}
    context.close()
    viewer.stop()
    assert viewer.process.returncode is not None


def test_first_open_splits_evenly_and_folds_the_ledger(lrs):
    page = lrs["page"]
    widths = {name: width_of(page, name) for name in ("source-pane", "markdown-pane", "ledger-inspector")}
    assert widths["ledger-inspector"] == 0, widths
    assert abs(widths["source-pane"] - widths["markdown-pane"]) <= 2, widths
    RESULTS["first open"] = widths


@pytest.mark.parametrize("style", ["highlight", "spotlight", "box", "blackout"])
def test_word_click_marks_its_grounding(lrs, style):
    page, index, markdown = lrs["page"], lrs["index"], lrs["markdown"]
    _, cell = lrs_cells(index, markdown)
    at = word_in_cell(index, cell)
    word = index["words"][at]
    page.click(f'[data-testid="style-picker"] [data-style="{style}"]')
    target = page.locator(f'[data-testid="markdown-pane"] [data-w="{at}"]').first
    target.scroll_into_view_if_needed()
    target.click()
    page.wait_for_timeout(1000)
    measured = mark_rect(page)
    assert measured, "no mark drawn"
    size = next(entry for entry in index["pages"] if entry["page"] == word["grounding"]["page"])
    result = near(measured, expected_box_rect(page, word["grounding"], size), f"word {markdown[word['range']['start']:word['range']['end']]!r}")
    assert visible_in(page, '[data-testid="source-pane"]', measured), "the mark was not scrolled into view"
    if style in ("spotlight", "blackout"):
        assert page.locator('[data-testid="source-pane"] svg.sh-veil').count() >= 1, f"{style} drew no veil"
    RESULTS[f"markdown word -> source, {style}"] = result


def test_lrs_header_sits_over_its_column(lrs):
    page, index, markdown = lrs["page"], lrs["index"], lrs["markdown"]
    header, value = lrs_cells(index, markdown)
    measured = page.evaluate("""([headerId, valueId]) => {
      const pane = document.querySelector('[data-testid="markdown-pane"]')
      const header = pane.querySelector(`[data-cell="${headerId}"]`)
      const value = pane.querySelector(`[data-cell="${valueId}"]`)
      if (!header || !value) return null
      const h = header.getBoundingClientRect()
      const v = value.getBoundingClientRect()
      return { headerText: header.textContent.trim(), headerLeft: h.left, headerRight: h.right,
               valueText: value.textContent.trim(), valueCentre: (v.left + v.right) / 2 }
    }""", [header["id"], value["id"]])
    assert measured, "header or value cell not rendered"
    assert (measured["headerText"], measured["valueText"]) == ("LRS-350-24", "350.4W")
    assert measured["headerLeft"] <= measured["valueCentre"] <= measured["headerRight"], measured
    RESULTS["LRS-350-24 header over 350.4W"] = {key: round(value, 1) if isinstance(value, float) else value for key, value in measured.items()}


def test_cell_click_marks_the_cell_grounding(lrs):
    page, index, markdown = lrs["page"], lrs["index"], lrs["markdown"]
    _, cell = lrs_cells(index, markdown)
    element = page.locator(f'[data-testid="markdown-pane"] [data-cell="{cell["id"]}"]')
    element.scroll_into_view_if_needed()
    box = element.bounding_box()
    page.mouse.click(box["x"] + 2, box["y"] + 2)  # a corner of the cell, off the word
    page.wait_for_timeout(1000)
    measured = mark_rect(page)
    assert measured, "no mark drawn"
    grounding = cell["grounding"][0]
    size = next(entry for entry in index["pages"] if entry["page"] == grounding["page"])
    RESULTS["table cell -> source"] = near(measured, expected_box_rect(page, grounding, size), cell["id"])


def test_source_click_lights_the_words_there(lrs):
    page, index, markdown = lrs["page"], lrs["index"], lrs["markdown"]
    _, cell = lrs_cells(index, markdown)
    at = word_in_cell(index, cell)
    word = index["words"][at]
    page.click(f'[data-testid="markdown-pane"] [data-w="{at}"]')  # bring its spot on screen first
    page.wait_for_timeout(1000)
    x0, y0, x1, y1 = word["grounding"]["box"]
    point = {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2}
    target = page.evaluate("""([number, point]) => {
      const rect = document.querySelector(`[data-testid="source-pane"] .sh-page[data-page="${number}"]`).getBoundingClientRect()
      return { x: rect.left + point.x * rect.width, y: rect.top + point.y * rect.height }
    }""", [word["grounding"]["page"], point])
    page.mouse.click(target["x"], target["y"])
    page.wait_for_timeout(1000)
    expected = sorted(index_at for index_at, candidate in enumerate(index["words"])
                      if (grounding := candidate.get("grounding")) and "box" in grounding
                      and grounding["page"] == word["grounding"]["page"]
                      and grounding["box"][0] <= point["x"] <= grounding["box"][2]
                      and grounding["box"][1] <= point["y"] <= grounding["box"][3])
    lit = page.evaluate("""() => [...new Set([...document.querySelectorAll('[data-testid="markdown-pane"] [data-selected]')]
      .map((span) => Number(span.dataset.w)))]""")
    assert sorted(lit) == expected, f"lit {lit}, expected {expected}"
    texts = page.evaluate("""() => [...document.querySelectorAll('[data-testid="markdown-pane"] [data-selected]')].map((span) => span.textContent)""")
    assert "".join(texts) == "".join(markdown[index["words"][i]["range"]["start"]:index["words"][i]["range"]["end"]] for i in expected)
    span = page.evaluate("""(at) => {
      const { left, top, right, bottom } = document.querySelector(`[data-testid="markdown-pane"] [data-w="${at}"]`).getBoundingClientRect()
      return { left, top, right, bottom }
    }""", expected[0])
    assert visible_in(page, '[data-testid="markdown-pane"]', span), "the lit word is not in view"
    size = next(entry for entry in index["pages"] if entry["page"] == word["grounding"]["page"])
    RESULTS["source -> markdown words"] = {"point": point, "lit": texts,
                                           **near(mark_rect(page), expected_box_rect(page, word["grounding"], size), "the clicked word, marked back")}


def test_raw_markdown_is_document_md_and_still_links(lrs):
    page, index, markdown = lrs["page"], lrs["index"], lrs["markdown"]
    _, cell = lrs_cells(index, markdown)
    at = word_in_cell(index, cell)
    word = index["words"][at]
    page.click('[data-testid="raw-toggle"]')
    page.wait_for_selector('[data-testid="markdown-pane"][data-mode="raw"]')
    shown = page.evaluate("""() => document.querySelector('[data-testid="markdown-pane"] pre').textContent""")
    assert shown == markdown, "the raw view is not document.md"
    target = page.locator(f'[data-testid="markdown-pane"] [data-w="{at}"]')
    target.scroll_into_view_if_needed()
    target.click()
    page.wait_for_timeout(1000)
    size = next(entry for entry in index["pages"] if entry["page"] == word["grounding"]["page"])
    RESULTS["raw word -> source"] = near(mark_rect(page), expected_box_rect(page, word["grounding"], size), "raw-mode word")
    page.click('[data-testid="raw-toggle"]')
    page.wait_for_selector('[data-testid="markdown-pane"][data-mode="rendered"]')


def test_kpi_strip_reads_kpis_json(lrs):
    page, kpis = lrs["page"], lrs["kpis"]
    text = page.locator('[data-testid="kpi-detail"]').inner_text()
    assert f"{kpis['time']['seconds']:.1f} s" in text, text
    for key, rung in kpis["ladder"].items():
        assert page.locator(f'[data-rung="{key}"]').inner_text().endswith(f"{rung['represented']}/{rung['seen']}")
    assert page.locator('[data-testid="kpi-headline"]').inner_text().startswith("Every word accounted for")
    RESULTS["kpi strip"] = " ".join(text.split())


def reload(page) -> None:
    page.reload()
    wait_ready(page)
    page.wait_for_timeout(300)


@pytest.mark.parametrize("bundle", [LRS, TPS, CUBEMARS], ids=["clean", "lost-and-unlinked", "lost-only"])
def test_kpi_text_never_says_null_or_undefined(browser, bundle):
    """WHY: the headline's missing parts went into replaceChildren as null, which writes the text "null" —
    CubeMars (27 lost, 0 unlinked) read "27 lostnull"."""
    viewer, context, page, errors = open_view(browser, bundle)
    try:
        for test_id in ("kpi-headline", "kpi-detail"):
            text = page.locator(f'[data-testid="{test_id}"]').inner_text()
            assert "null" not in text and "undefined" not in text, f"{test_id}: {text!r}"
        assert errors == [], errors
    finally:
        context.close()
        viewer.stop()


def test_drag_source_boundary_and_it_survives_a_reload(lrs):
    page = lrs["page"]
    before = width_of(page, "source-pane")
    drag(page, "splitter-source-output", -220)
    after = width_of(page, "source-pane")
    assert abs(after - before + 220) <= 3, f"dragged -220 px but the pane went {before:.1f} -> {after:.1f}"
    reload(page)
    reloaded = width_of(page, "source-pane")
    assert abs(reloaded - after) <= 2, f"after reload {reloaded:.1f} != {after:.1f}"
    RESULTS["drag source|markdown"] = {"before": round(before, 1), "after": round(after, 1), "after_reload": round(reloaded, 1)}


def test_source_refits_after_a_drag(lrs):
    page = lrs["page"]
    drag(page, "splitter-source-output", 120)
    page.wait_for_timeout(400)  # the 200 ms settle, then the pane reports "fitting" until pdf.js has drawn every page
    wait_ready(page)
    fit = page.evaluate("""() => {
      const pane = document.querySelector('[data-testid="source-pane"]')
      const pages = [...pane.querySelectorAll('.sh-page')]
      const style = getComputedStyle(pane)
      return {
        pages: pages.length,
        drawn: pages.filter((element) => {
          const canvas = element.querySelector('canvas')
          if (!canvas) return false
          const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
          for (let index = 0; index < pixels.length; index += 4 * 97) if (pixels[index] < 128) return true
          return false
        }).length,
        pageWidth: pages[0]?.getBoundingClientRect().width,
        available: pane.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
      }
    }""")
    assert fit["drawn"] == fit["pages"], fit
    assert abs(fit["pageWidth"] - fit["available"]) <= 1, fit
    RESULTS["refit after drag"] = fit


def test_ledger_inspector_folds_drags_and_survives_a_reload(lrs):
    page = lrs["page"]
    assert width_of(page, "ledger-inspector") == 0
    page.click('[data-testid="inspector-toggle"]')
    page.wait_for_timeout(200)
    opened = width_of(page, "ledger-inspector")
    assert opened >= 199, opened
    drag(page, "splitter-output-inspector", -120)
    dragged = width_of(page, "ledger-inspector")
    assert abs(dragged - opened - 120) <= 3, f"{opened:.1f} -> {dragged:.1f} for a -120 px drag"
    reload(page)
    reloaded = width_of(page, "ledger-inspector")
    assert abs(reloaded - dragged) <= 2, f"after reload {reloaded:.1f} != {dragged:.1f}"
    page.click('[data-testid="inspector-toggle"]')
    page.wait_for_timeout(200)
    folded = width_of(page, "ledger-inspector")
    assert folded == 0, folded
    RESULTS["drag ledger"] = {"opened": round(opened, 1), "dragged": round(dragged, 1), "after_reload": round(reloaded, 1), "folded": folded}


def test_kpi_strip_folds_and_stays_folded(lrs):
    page = lrs["page"]
    height = lambda: page.evaluate("""() => document.querySelector('[data-testid="kpi-strip"]').getBoundingClientRect().height""")  # noqa: E731
    opened = height()
    page.click('[data-testid="kpi-fold"]')
    page.wait_for_timeout(100)
    folded = height()
    assert folded < opened - 20, (opened, folded)
    reload(page)
    reloaded = height()
    assert abs(reloaded - folded) <= 1, (folded, reloaded)
    page.click('[data-testid="kpi-fold"]')
    RESULTS["fold kpi strip"] = {"open": opened, "folded": folded, "after_reload": reloaded}
    assert lrs["errors"] == [], lrs["errors"]


# ---- the TI TPS54331: a datasheet with real losses -------------------------------------------------------------

def test_ledger_detail_on_the_tps54331(browser):
    viewer, context, page, errors = open_view(browser, TPS)
    try:
        index, ledger, _, _ = load(TPS)
        page.click('[data-testid="inspector-toggle"]')
        # Opening the ledger narrows the source, which re-fits and redraws all 41 pages.
        page.wait_for_timeout(400)
        wait_ready(page)
        lost = ledger["source"]["lost_words"][0]
        page.click('[data-testid="ledger-inspector"] [data-lost="0"]')
        page.wait_for_timeout(1500)
        measured = mark_rect(page)
        assert measured, "no mark for the lost word"
        size = next(entry for entry in index["pages"] if entry["page"] == lost["grounding"]["page"])
        RESULTS["tps54331 lost word -> source"] = {"lost_words": len(ledger["source"]["lost_words"]), "first": lost["text"],
                                                   **near(measured, expected_box_rect(page, lost["grounding"], size), f"lost {lost['text']!r}")}
        page.click('[data-testid="ledger-inspector"] [data-unlinked-item="0"]')
        page.wait_for_timeout(1000)
        lit = page.evaluate("""() => {
          const span = document.querySelector('[data-testid="markdown-pane"] [data-selected]')
          if (!span) return null
          const { left, top, right, bottom } = span.getBoundingClientRect()
          return { text: span.textContent, unlinked: span.hasAttribute('data-unlinked'), rect: { left, top, right, bottom } }
        }""")
        assert lit and lit["unlinked"], lit
        assert lit["text"] == ledger["markdown"]["unlinked_words"][0]["text"]
        assert visible_in(page, '[data-testid="markdown-pane"]', lit["rect"]), "the unlinked word is not in view"
        RESULTS["tps54331 unlinked word lit"] = {"unlinked_words": len(ledger["markdown"]["unlinked_words"]), "first": lit["text"]}
        assert errors == [], errors
    finally:
        context.close()
        viewer.stop()


# ---- an archived web page: the html Location path --------------------------------------------------------------

def test_html_word_marks_the_page_and_back(browser):
    viewer, context, page, errors = open_view(browser, CUBEMARS)
    try:
        index, _, _, markdown = load(CUBEMARS)
        # A word whose quote occurs once in its text node, so the oracle below finds the same place.
        at = next(i for i, word in enumerate(index["words"])
                  if (quote := (word.get("grounding") or {}).get("html", {}).get("text_quote")) and quote["exact"] == "Torque"
                  and quote.get("prefix") and quote.get("suffix"))
        quote = index["words"][at]["grounding"]["html"]["text_quote"]
        page.click('[data-testid="style-picker"] [data-style="box"]')
        target = page.locator(f'[data-testid="markdown-pane"] [data-w="{at}"]')
        target.scroll_into_view_if_needed()
        target.click()
        page.wait_for_timeout(1200)
        measured = mark_rect(page)
        assert measured, "no mark on the html page"
        # Independent oracle: the browser's own Range over the quoted text inside the page, padded the same way.
        expected = page.evaluate("""([quote, padding]) => {
          const frame = document.querySelector('[data-testid="source-pane"] iframe')
          const doc = frame.contentDocument
          const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
          const needle = quote.prefix + quote.exact + quote.suffix
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const found = node.data.indexOf(needle)
            if (found < 0) continue
            const start = found + quote.prefix.length
            const range = doc.createRange()
            range.setStart(node, start)
            range.setEnd(node, start + quote.exact.length)
            const inner = range.getBoundingClientRect()
            const outer = frame.getBoundingClientRect()
            const zoom = outer.width / frame.offsetWidth
            return {
              left: outer.left + (inner.left - padding) * zoom,
              top: outer.top + (inner.top - padding) * zoom,
              right: outer.left + (inner.right + padding) * zoom,
              bottom: outer.top + (inner.bottom + padding) * zoom,
            }
          }
          return null
        }""", [quote, PADDING])
        assert expected, f"oracle could not find {quote}"
        result = near(measured, expected, f"html word {quote['exact']!r}")
        page.mouse.click((measured["left"] + measured["right"]) / 2, (measured["top"] + measured["bottom"]) / 2)
        page.wait_for_timeout(700)
        lit = page.evaluate("""() => [...new Set([...document.querySelectorAll('[data-testid="markdown-pane"] [data-selected]')]
          .map((span) => Number(span.dataset.w)))]""")
        assert lit == [at], f"clicking the mark lit {lit}, expected [{at}]"
        RESULTS["html word -> page -> word"] = {"word": quote["exact"], "lit": lit, **result}
        assert errors == [], errors
    finally:
        context.close()
        viewer.stop()


def teardown_module(module) -> None:
    if RESULTS:
        MEDIA.mkdir(parents=True, exist_ok=True)
        (MEDIA / "journey-results.json").write_text(json.dumps({"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": RESULTS},
                                                               indent=2, default=str), encoding="utf-8")


# ---- the hero video and the screenshots (`python tests/test_view_journey.py --media`) --------------------------

def record_media() -> None:
    """Open → click a table value → the source spotlights it, recorded from the real page on a fresh profile."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    index, ledger, _, markdown = load(LRS)
    _, cell = lrs_cells(index, markdown)
    at = word_in_cell(index, cell)
    efficiency = next(block for block in index["blocks"] if block["type"] == "table_cell"
                      and markdown[block["range"]["start"]:block["range"]["end"]] == "89%")
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="markdownify-view-media-"))
    viewer = Viewer(LRS)
    try:
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            size = {"width": 1280, "height": 800}
            context = browser.new_context(viewport=size, record_video_dir=str(scratch), record_video_size=size)
            page = context.new_page()
            started = time.monotonic()
            page.goto(viewer.url)
            wait_ready(page)
            page.wait_for_timeout(1200)
            page.click('[data-testid="style-picker"] [data-style="spotlight"]')
            page.wait_for_timeout(400)
            for word in (at, word_in_cell(index, efficiency)):
                page.evaluate("""(at) => document.querySelector(`[data-testid="markdown-pane"] [data-w="${at}"]`)
                  .scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })""", word)
                page.wait_for_timeout(1100)
                box = page.locator(f'[data-testid="markdown-pane"] [data-w="{word}"]').bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
                page.wait_for_timeout(250)
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(2300)
                if word == at:
                    page.screenshot(path=str(MEDIA / "01-table-value-spotlit.png"))
            seconds = time.monotonic() - started
            video = page.video
            context.close()
            webm = MEDIA / "hero-view.webm"
            shutil.move(video.path(), webm)

            # The second still: a datasheet with real losses, its ledger open on a lost word.
            tps = Viewer(TPS)
            try:
                still = browser.new_context(viewport=size)
                page = still.new_page()
                page.goto(tps.url)
                wait_ready(page)
                page.click('[data-testid="inspector-toggle"]')
                page.wait_for_timeout(400)
                wait_ready(page)
                page.click('[data-testid="ledger-inspector"] [data-lost="0"]')
                page.wait_for_timeout(1500)
                page.screenshot(path=str(MEDIA / "02-ledger-lost-word.png"))
                still.close()
            finally:
                tps.stop()
            browser.close()
    finally:
        viewer.stop()
        shutil.rmtree(scratch, ignore_errors=True)
    # Trim the blank page load off the front, keep it muted, and make the GIF fallback.
    length = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(webm)]))
    start = max(0.0, length - seconds)
    mp4 = MEDIA / "hero-view.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(webm), "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-vf",
                    "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", str(MEDIA / "hero-view.gif")], check=True)
    duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)]))
    print(f"hero video {duration:.1f} s: {mp4}")
    assert 6 <= duration <= 15, f"hero video is {duration:.1f} s"


if __name__ == "__main__":
    status = pytest.main([__file__, "-q", "-s"])
    if "--media" in sys.argv[1:] and status == 0:
        record_media()
    sys.exit(status)
