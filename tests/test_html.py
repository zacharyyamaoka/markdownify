"""mdify.html on a small maker-style page whose answers are known, plus the real CubeMars pages in robotparts'
ingest cache when this machine has them. Run: uv run pytest tests/test_html.py"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

import pytest

from mdify import html as mdify_html
from mdify.align import markdown_words, normalize
from mdify.converters import Source

# A product page shaped like CubeMars': a header + nav listing the whole catalogue, a spec table with a row header
# column and a merged cell, a footer. Non-ASCII and an emoji come first so code points and UTF-16 units disagree,
# and the line endings are CRLF.
PAGE = """<!doctype html>\r
<html><head><meta charset="utf-8"><title>AK70-10 KV100 – Maker</title><script>var menu = "AK10-9";</script></head>\r
<body>\r
<header class="site-header"><div class="logo">温度 😀 Maker Motors</div>\r
<nav><ul>\r
  <li><a href="/p/ak10-9">AK10-9 KV60</a></li>\r
  <li><a href="/p/ake90-8">AKE90-8 KV35</a></li>\r
  <li><a href="/p/ri50">RI50 KV100</a></li>\r
</ul></nav></header>\r
<main><article>\r
<h1>AK70-10 KV100 Robotic Actuator</h1>\r
<p>The AK70-10 joins a <b>10</b>:1 planetary gearbox to a 24V motor. Rated torque 8.3&nbsp;Nm and peak torque 24.8 N&middot;m.</p>\r
<p>It weighs 521g and ships with a CAN cable. Its encoder resolves 14 bits per turn, so the output position is known
after power-up without homing. Each actuator is tested on a dynamometer before it leaves the factory.</p>\r
<div style="display:none">Hidden promo text</div>\r
<div style="visibility: hidden" class="wow">Revealed on scroll</div>\r
<table>\r
<thead><tr><th scope="col">Parameter</th><th scope="col">Value</th><th scope="col">Unit</th></tr></thead>\r
<tbody>\r
<tr><th scope="row">Rated torque</th><td>8.3</td><td rowspan="2">N·m</td></tr>\r
<tr><th scope="row">Peak torque</th><td>24.8</td></tr>\r
<tr><th scope="row">Weight</th><td colspan="2">521 g</td></tr>\r
<tr><th scope="row">Notes</th><td></td><td>-</td></tr>\r
<tr><th scope="row">Mode</th><th>MIT</th><th>Servo</th></tr>\r
</tbody></table>\r
</article></main>\r
<footer><p>Copyright 2026 Maker Motors. All rights reserved.</p></footer>\r
</body></html>\r
"""

URL = "https://maker.example/p/ak70-10"


@pytest.fixture(scope="module")
def page_source(tmp_path_factory) -> Source:
    path = tmp_path_factory.mktemp("html") / "source.html"
    data = PAGE.encode("utf-8")
    path.write_bytes(data)
    return Source(path, hashlib.sha256(data).hexdigest(), "text/html", URL)


@pytest.fixture(scope="module")
def conversion(page_source):
    return mdify_html.convert(page_source)


def _word_texts(conversion):
    return {word.start: word.text for word in markdown_words(conversion.markdown)}


def test_every_markdown_word_is_linked_and_faithful(page_source, conversion):
    texts = _word_texts(conversion)
    assert conversion.words, conversion.markdown
    for word in conversion.words:
        markdown_text = texts[word["range"]["start"]]
        assert word["grounding"] is not None, markdown_text
        assert word["score"] == 1.0, markdown_text
        assert normalize(mdify_html.text_under(page_source, word["grounding"])) == normalize(markdown_text)


def test_ranges_are_code_points_into_the_decoded_html(page_source, conversion):
    decoded = mdify_html.decoded_html(page_source)
    texts = _word_texts(conversion)
    by_text = {texts[word["range"]["start"]]: word for word in conversion.words}
    rated = by_text["Rated"]["grounding"]["html"]["source_range"]
    assert decoded[rated["start"]:rated["end"]] == "Rated"
    # An entity is part of the word it sits in: "N&middot;m" is one word, decoded "N·m".
    newton_metre = by_text["N·m"]["grounding"]["html"]["source_range"]
    assert decoded[newton_metre["start"]:newton_metre["end"]] in ("N&middot;m", "N·m")


def test_inline_markup_does_not_split_a_word(page_source):
    words = [word.text for word in mdify_html.inventory(page_source)]
    assert "10:1" in words          # <b>10</b>:1 reads as one word on screen


def test_text_quote_carries_its_neighbours(conversion):
    texts = _word_texts(conversion)
    quotes = [word["grounding"]["html"]["text_quote"] for word in conversion.words
              if texts[word["range"]["start"]] == "8.3" and word["grounding"]["html"]["xpath"].endswith("/p[1]")]
    assert quotes == [{"exact": "8.3", "prefix": "motor. Rated torque ", "suffix": " Nm and peak"}]


def test_navigation_menu_and_footer_are_dropped_with_reasons(page_source, conversion):
    for name in ("AKE90-8", "RI50", "Copyright"):
        assert name not in conversion.markdown
    reasons = {drop["reason"] for drop in conversion.dropped}
    assert {"navigation", "footer"} <= reasons or {"header", "footer"} <= reasons
    decoded = mdify_html.decoded_html(page_source)
    menu_entry = decoded.index("AKE90-8 KV35")
    covering = [drop for drop in conversion.dropped
                if drop["grounding"]["html"]["source_range"]["start"] <= menu_entry < drop["grounding"]["html"]["source_range"]["end"]]
    assert [drop["reason"] for drop in covering] in (["navigation"], ["header"])


def test_inventory_is_visible_text_only(page_source):
    words = [word.text for word in mdify_html.inventory(page_source)]
    assert "Hidden" not in words           # inline display:none
    assert "AK10-9\";" not in words and "menu" not in words   # <script>
    assert "Revealed" in words             # visibility:hidden is a scroll-reveal state, read as visible
    assert "AKE90-8" in words              # the menu is visible; Defuddle drops it, the inventory keeps it


def test_table_cells_have_grid_positions_and_header_kinds(conversion):
    blocks = {block["id"]: block for block in conversion.blocks}
    tables = [block for block in conversion.blocks if block["type"] == "table"]
    assert len(tables) == 1
    table = tables[0]
    cells = [blocks[child] for child in table["children"]]
    texts = {(cell["row"], cell["col"]): conversion.markdown[cell["range"]["start"]:cell["range"]["end"]] for cell in cells}
    assert texts[(0, 0)] == "Parameter" and texts[(1, 0)] == "Rated torque" and texts[(1, 2)] == "N·m"
    by_position = {(cell["row"], cell["col"]): cell for cell in cells}
    assert by_position[(0, 0)]["kind"] == "stubHead"       # the header cell above the row headers
    assert by_position[(0, 1)]["kind"] == "columnHeader"
    assert by_position[(1, 0)]["kind"] == "rowHeader"
    assert by_position[(1, 1)]["kind"] == "content"
    assert by_position[(1, 2)]["rowspan"] == 2
    assert by_position[(3, 1)]["colspan"] == 2
    assert by_position[(5, 0)]["kind"] == "rowHeader"      # scope="row" in a row of headers only: scope wins
    assert by_position[(4, 1)]["range"]["start"] == by_position[(4, 1)]["range"]["end"]   # the empty cell
    for cell in cells:
        assert table["range"]["start"] <= cell["range"]["start"] <= cell["range"]["end"] <= table["range"]["end"]
        assert cell["grounding"][0]["html"]["xpath"].split("/")[-1].startswith(("td", "th"))


def test_block_ids_are_unique_and_ranges_nest(conversion):
    ids = [block["id"] for block in conversion.blocks]
    assert len(ids) == len(set(ids))
    blocks = {block["id"]: block for block in conversion.blocks}
    for block in conversion.blocks:
        for child in block.get("children", []):
            inner = blocks[child]["range"]
            assert block["range"]["start"] <= inner["start"] <= inner["end"] <= block["range"]["end"]


def test_markdown_is_defuddles_own(page_source, conversion):
    """The stamped ids must not change what Defuddle writes: its markdown for the unstamped page is ours."""
    script = """
import { Defuddle } from 'defuddle/node';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const dist = path.dirname(fileURLToPath(import.meta.resolve('defuddle/node')));
const { parseLinkedomHTML } = require(path.join(dist, 'utils', 'linkedom-compat.js'));
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const { html, url } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const result = await Defuddle(parseLinkedomHTML(html, url), url, { markdown: true, useAsync: false });
process.stdout.write(result.content);
"""
    payload = json.dumps({"html": mdify_html.decoded_html(page_source), "url": URL}).encode()
    run = subprocess.run([mdify_html.NODE, "--input-type=module", "-e", script], input=payload, capture_output=True,
                         cwd=mdify_html.TOOL.parent, check=True)
    assert run.stdout.decode("utf-8") == conversion.markdown


# --- the real set: robotparts' cached pages ----------------------------------------------------------------------
CACHE = pathlib.Path.home() / ".cache/robotparts-ingest/blobs"
CUBEMARS_PAGES = [
    # the newer template (Defuddle keeps only the description <article>) and the older one (spec table kept)
    "07e6ce25416c54cd4bc48ed3fe204824ef1265cff22ce944731b203b2a10226c",
    "578b57a2b8ae",
]


def _cached(prefix: str) -> Source | None:
    if not CACHE.is_dir():
        return None
    for sidecar in CACHE.glob(f"{prefix}*.json"):
        meta = json.loads(sidecar.read_text())
        return Source(CACHE / meta["sha256"], meta["sha256"], meta["media_type"], meta["retrievals"][0]["url"])
    return None


@pytest.mark.parametrize("prefix", CUBEMARS_PAGES)
def test_cubemars_catalogue_menu_is_dropped_as_navigation(prefix):
    source = _cached(prefix)
    if source is None:
        pytest.skip("robotparts ingest cache not on this machine")
    conversion = mdify_html.convert(source)
    decoded = mdify_html.decoded_html(source)
    navigation = [drop["grounding"]["html"]["source_range"] for drop in conversion.dropped if drop["reason"] == "navigation"]
    menu = decoded.index("<nav")
    assert any(span["start"] <= menu < span["end"] for span in navigation)
    # No menu entry reaches the markdown as a bullet ("- AKE90-8 KV35" is how the menu writes it).
    assert "\\- AKE90-8" not in conversion.markdown and "- AKE90-8" not in conversion.markdown
    texts = _word_texts(conversion)
    for word in conversion.words:
        if word["grounding"] is not None and word["score"] == 1.0:
            under = normalize(mdify_html.text_under(source, word["grounding"]))
            assert under == normalize(texts[word["range"]["start"]])
