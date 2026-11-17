"""Pipeline A for web pages (CONTRACT §3.3): Defuddle picks the content and writes the markdown, and every markdown
word is grounded as a code-point range into the decoded HTML of `source.<ext>`, with its xpath and a text quote.

The work is split at the language line:
- `html_tool/markdownify_html.mjs` (Node: Defuddle + jsdom) knows the DOM. It returns Defuddle's markdown, each block's
  range in it with the element it came from, the page's visible words with where each sat in the HTML, and the
  subtrees Defuddle removed.
- This module turns that into the contract's shapes and aligns markdown words to source words with `markdownify.align`,
  block by block: a block's markdown words are matched against the visible words inside its own element first.
  WHY block by block: CubeMars prints its whole catalogue in its menu, so "AK70-10" occurs many times on the page;
  matched against the whole page a spec-table cell could be pinned to a menu entry.

Content mode. By default Defuddle only writes and standardises the markdown of the whole <body>; what is left out
is decided in `html_tool` by known reasons alone: page landmarks — <nav>/<menu> or a navigation/menu role
(navigation), the page's <header>/banner (header), its <footer>/contentinfo (footer), <aside>/complementary
(boilerplate, detail "aside") — elements hidden in markup, and second copies (a menu repeated in a mobile drawer, a
block rendered twice: duplicate). Everything else is kept, tab panels and short lists included.
WHY (coordinator, 2026-10-29, from this converter's own ledger on robotparts' 46 cached maker pages): left to pick
the content, Defuddle kept 0.51 of the visible words — CubeMars lost its whole Specification tab to the description
<article>, mdmbot its rated/peak torque line. Losing a spec table is data loss; keeping a bit of boilerplate is
cheap. `convert(source, reader=True)` (`markdownify_html.mjs convert --reader`) is Defuddle's own reading mode; there a
removal counts as dropped only when its markup says why, and every other removed word stays `lost`.

The inventory (`inventory`) is the page's visible text nodes, read by jsdom alone — `markdownify_html.mjs inventory`
never loads Defuddle — so the ledger is not graded by the code it grades. "Visible" is markup-only for v0: no
<head>/script/style/template, no svg tooltip or definitions (an svg's drawn <text> is visible), no `hidden`, no
inline display:none (visibility:hidden counts as visible: scroll-reveal scripts ship real content that way); CSS
classes are not evaluated.
"""

from __future__ import annotations

import bisect
import codecs
import difflib
import functools
import html
import json
import pathlib
import re
import subprocess

from rapidfuzz.distance import Levenshtein

from markdownify.align import align_word_spans, markdown_words, normalize
from markdownify.converters import Conversion, Source, SourceWord

MEDIA_TYPES = ("text/html", "application/xhtml+xml")
PIPELINE = "A"

NODE = "/usr/bin/node"  # WHY absolute: the first `node` on this machine's PATH is an old balena copy (v8-era)
TOOL = pathlib.Path(__file__).resolve().parents[2] / "html_tool" / "markdownify_html.mjs"
# WHY re-score: the aligner scores a word against the characters it matched, which can sit in two source words
# far apart ("M" of "Maximum" + "ode" of "Model" = "Mode", 1.0). The score written is the markdown word against
# the text its grounding actually covers, so invariant 1 holds by construction; below MIN_SCORE it is unlinked.
# WHY 0.9 and not align's 0.5: Defuddle copies page text verbatim, so a real match here is exact or nearly so; a
# 0.5 match is scattered letters of unrelated words, and accepting one inside a block claims those words away
# from the word that really owns them.
MIN_SCORE = 0.9
QUOTE_WORDS = 3          # words of context on each side of a text quote, within the same box

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_TAG_OR_COMMENT = re.compile(r"<!--.*?-->|<[^>]*>", re.S)
_ENTITY = re.compile(r"&(#[xX][0-9a-fA-F]+;?|#[0-9]+;?|[A-Za-z][A-Za-z0-9]*;?)")  # as the HTML tokenizer reads one


# --- the decoded page -------------------------------------------------------------------------------------------
def _charset(data: bytes) -> str:
    """BOM, then <meta charset> in the first 4 KiB, then UTF-8 — the order a browser sniffs a saved page in.
    The HTTP header's charset would come first in a browser, but `Source` does not carry it (CONTRACT §2's
    source.json has the retrieval, the converter seam has not)."""
    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return name
    match = _META_CHARSET.search(data[:4096])
    if match:
        name = match.group(1).decode("ascii", "replace")
        try:
            codecs.lookup(name)
            return name
        except LookupError:
            pass
    return "utf-8"


@functools.lru_cache(maxsize=8)
def _decoded(path: str, sha256: str) -> str:
    data = pathlib.Path(path).read_bytes()
    return data.decode(_charset(data), errors="replace")


def decoded_html(source: Source) -> str:
    """The string every `source_range` counts code points in (CONTRACT §3.3)."""
    return _decoded(str(source.path), source.sha256)


def _run(mode: str, source: Source, *flags: str) -> dict:
    payload = json.dumps({"html": decoded_html(source), "url": source.url or ""}).encode("utf-8")
    run = subprocess.run([NODE, str(TOOL), mode, *flags], input=payload, capture_output=True, check=False,
                         cwd=TOOL.parent)
    if run.returncode != 0:
        raise RuntimeError(f"html_tool {mode} failed ({run.returncode}): {run.stderr.decode('utf-8', 'replace')[-2000:]}")
    return json.loads(run.stdout)


# --- source words -----------------------------------------------------------------------------------------------
class _Page:
    """The visible words of one page, with what grounding a word (or part of one) needs: per-character source
    offsets and the neighbouring words of the same box for a text quote."""

    def __init__(self, decoded: str, words: list[dict]):
        self.decoded = decoded
        self.words = words

    def char_offsets(self, index: int) -> list[tuple[int, int]]:
        """[start, end) in the decoded HTML of each character of word `index`. A word written plainly ("Peak")
        is its range character by character; one with an entity or a tag inside ("N&middot;m", "<b>20</b>Nm") is
        re-read from the HTML, an entity's characters all pointing at the whole entity."""
        word = self.words[index]
        start, end = word["source_range"]["start"], word["source_range"]["end"]
        text = word["text"]
        if end - start == len(text):
            return [(start + k, start + k + 1) for k in range(len(text))]
        offsets: list[tuple[int, int]] = []
        position = start
        raw = self.decoded
        while position < end and len(offsets) < len(text):
            if raw[position] == "<":
                close = raw.find(">", position)
                position = end if close < 0 else close + 1
                continue
            if raw[position] == "&":
                match = _ENTITY.match(raw, position, end)
                if match:
                    decoded = html.unescape(match.group(0))
                    if decoded != match.group(0):
                        offsets.extend([(position, match.end())] * len(decoded))
                        position = match.end()
                        continue
            if raw[position].isspace():
                position += 1
                continue
            offsets.append((position, position + 1))
            position += 1
        if len(offsets) != len(text):
            # The parser read it differently from this walk (rare: a NUL, a stray CR): the whole word, every char.
            offsets = [(start, end)] * len(text)
        return offsets[:len(text)]

    def grounding(self, index: int, first: int | None = None, last: int | None = None) -> dict:
        """The §3.3 grounding of word `index`, or of its characters [first, last) when a markdown word covers only
        part of it."""
        word = self.words[index]
        text = word["text"]
        first = 0 if first is None else first
        last = len(text) if last is None else last
        if (first, last) == (0, len(text)):
            source_range = dict(word["source_range"])
        else:
            offsets = self.char_offsets(index)
            source_range = {"start": offsets[first][0], "end": offsets[last - 1][1]}
        return {"html": {"source_range": source_range, "xpath": word["xpath"],
                         "text_quote": self.quote(index, first, last)}}

    def quote(self, index: int, first: int, last: int) -> dict:
        """W3C TextQuoteSelector-style exact/prefix/suffix, the context taken from the same box only (a cell, a
        paragraph) so a Text Fragment link does not reach across a layout boundary."""
        word = self.words[index]
        before, after = [], []
        for k in range(index - 1, max(-1, index - 1 - QUOTE_WORDS), -1):
            if self.words[k]["box"] != word["box"]:
                break
            before.insert(0, self.words[k]["text"])
        for k in range(index + 1, min(len(self.words), index + 1 + QUOTE_WORDS)):
            if self.words[k]["box"] != word["box"]:
                break
            after.append(self.words[k]["text"])
        text = word["text"]
        prefix = (" ".join(before) + " " if before else "") + text[:first]
        suffix = text[last:] + (" " + " ".join(after) if after else "")
        return {"exact": text[first:last], "prefix": prefix, "suffix": suffix}


def inventory(source: Source) -> list[SourceWord]:
    output = _run("inventory", source)
    page = _Page(decoded_html(source), output["inventory"])
    return [SourceWord(word["text"], page.grounding(index)) for index, word in enumerate(page.words)]


def visible_page(source: Source) -> _Page:
    """The inventory's words with their per-character offsets: `markdownify.capture_snapshot` keys each one by xpath and
    ordinal to find its measured box."""
    return _Page(decoded_html(source), _run("inventory", source)["inventory"])


def text_under(source: Source, grounding: dict) -> str:
    """The text at `source_range` as a reader sees it: tags and comments removed, entities decoded.

    A grounding that also carries a `snapshot` box (CONTRACT §3.3) is one claim in two places, so both must hold
    the text: when the words measured under the box (snapshot.json beside the source) do not contain it, the text
    returned says what the box holds instead, and `markdownify check` reports the word as not faithful."""
    source_range = grounding["html"]["source_range"]
    fragment = decoded_html(source)[source_range["start"]:source_range["end"]]
    text = html.unescape(_TAG_OR_COMMENT.sub("", fragment))
    if "snapshot" in grounding:
        from markdownify.capture_snapshot import disagreement

        held = disagreement(pathlib.Path(source.path).parent, grounding["snapshot"], text)
        if held is not None:
            return f"{text} [snapshot box holds {held!r}]"
    return text


# --- conversion -------------------------------------------------------------------------------------------------
def _range_mapper(marked: str, reference: str):
    """A function taking an offset in `marked` (the sentinel run) to the same place in `reference` (Defuddle's
    markdown). They differ only where a sentinel hid a line start from Turndown's escaping ("1." vs "1\\."), so the
    equal blocks of a character diff carry every block boundary across."""
    matcher = difflib.SequenceMatcher(None, marked, reference, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    starts = [block.a for block in blocks]

    def to_reference(offset: int) -> int:
        k = bisect.bisect_right(starts, offset) - 1
        if k < 0:
            return 0
        block = blocks[k]
        return block.b + min(offset - block.a, block.size)
    return to_reference


def _block_records(output: dict) -> list[dict]:
    """Blocks with their range in the reference markdown, children, and contract ids, in markdown order."""
    raw_blocks = output["blocks"]
    marked = output.get("marked_markdown")
    to_reference = _range_mapper(marked, output["reference_markdown"]) if marked is not None else (lambda offset: offset)
    placed = [block for block in raw_blocks if block["markdown_range"] is not None]
    for block in placed:
        block["range"] = {"start": to_reference(block["markdown_range"]["start"]),
                          "end": to_reference(block["markdown_range"]["end"])}
    placed.sort(key=lambda block: (block["range"]["start"], -block["range"]["end"], block["depth"]))
    for serial, block in enumerate(placed, start=1):
        block["id"] = f"{block['type']}-{serial}"
    by_number = {block["number"]: block for block in placed}
    for block in placed:
        block["children"] = []
    for block in placed:
        parent = block["parent"]
        while parent is not None and parent not in by_number:
            parent = raw_blocks[parent]["parent"]   # a parent with no place in the markdown: its own parent
        if parent is not None:
            by_number[parent]["children"].append(block["id"])
    return placed


def convert(source: Source, *, reader: bool = False) -> Conversion:
    """`reader=True` is Defuddle's reading mode (it picks the content); the default keeps everything that has no
    known reason to go — see the module docstring."""
    output = _run("convert", source, *(["--reader"] if reader else []))
    markdown = output["reference_markdown"]
    decoded = decoded_html(source)
    # Alignment sees the visible words plus image alt text (Defuddle writes alt as a caption); only the visible
    # words are the inventory, so alt words never count on the ledger's source side.
    page = _Page(decoded, output["inventory"] + output["extra_words"])
    source_words = page.words

    # Source words not left out are the alignment candidates; a left-out word can still be found by the residual
    # pass, but never ahead of a kept one. In the default mode only a removal with a reason left anything out: a
    # subtree with no surviving id and no reason is one Defuddle's standardisation rebuilt (a <div> turned <p>, a
    # <span> unwrapped), and its words are in the markdown. In reader mode such a subtree is Defuddle's own cut.
    removed = sorted((region["source_range"]["start"], region["source_range"]["end"]) for region in output["dropped"]
                     if reader or region["reason"] is not None)
    removed_starts = [start for start, _ in removed]

    def is_removed(word: dict) -> bool:
        start, end = word["source_range"]["start"], word["source_range"]["end"]
        k = bisect.bisect_right(removed_starts, start) - 1
        return k >= 0 and removed[k][0] <= start and end <= removed[k][1]

    reading_order = sorted(range(len(source_words)), key=lambda index: source_words[index]["source_range"]["start"])
    candidate = [index for index in reading_order if not is_removed(source_words[index])]
    word_starts = [source_words[index]["source_range"]["start"] for index in reading_order]

    blocks = _block_records(output)
    md_words = markdown_words(markdown)
    md_starts = [word.start for word in md_words]

    # One alignment group per innermost block: its markdown words against the kept visible words in its element.
    candidate_set = set(candidate)
    groups = []
    for block in blocks:
        if block["children"] or block["source_range"] is None:
            continue
        low = bisect.bisect_left(md_starts, block["range"]["start"])
        high = bisect.bisect_left(md_starts, block["range"]["end"])
        inside = [i for i in range(low, high) if md_words[i].end <= block["range"]["end"]]
        source_start, source_end = block["source_range"]["start"], block["source_range"]["end"]
        first = bisect.bisect_left(word_starts, source_start)
        owned = []
        for position in range(first, len(reading_order)):
            if word_starts[position] >= source_end:
                break
            j = reading_order[position]
            if source_words[j]["source_range"]["end"] <= source_end and j in candidate_set:
                owned.append(j)
        if inside and owned:
            groups.append((inside, owned))
    # The residual pass (inside align_word_spans) runs over every source word; offering the kept ones as a group
    # first keeps a leftover markdown word off the menu's copy of itself.
    groups.append((list(range(len(md_words))), candidate))
    alignments = align_word_spans([word.text for word in md_words], [word["text"] for word in source_words],
                                  groups=groups, min_score=MIN_SCORE)

    words = []
    for md_word, alignment in zip(md_words, alignments):
        row = {"range": {"start": md_word.start, "end": md_word.end}, "grounding": None, "score": 0.0}
        if alignment.sources:
            grounding = _grounding_of(page, alignment.sources)
            score = _score(md_word.text, text_of_range(decoded, grounding["html"]["source_range"]))
            if score >= MIN_SCORE:
                row["grounding"], row["score"] = grounding, score
        words.append(row)

    contract_blocks = []
    for block in blocks:
        record = {"id": block["id"], "type": block["type"], "range": block["range"],
                  "represented": block["represented"]}
        record["grounding"] = ([{"html": {"source_range": block["source_range"], "xpath": block["xpath"]}}]
                               if block["source_range"] is not None else _grounding_from_words(words, block["range"]))
        if block["children"]:
            record["children"] = block["children"]
        if block["type"] == "table_cell":
            for key in ("row", "col", "rowspan", "colspan", "kind"):
                if key in block:
                    record[key] = block[key]
        contract_blocks.append(record)

    dropped = [{"grounding": {"html": {"source_range": region["source_range"], "xpath": region["xpath"]}},
                "reason": region["reason"], "detail": region["detail"]}
               for region in output["dropped"] if region["reason"] is not None]
    return Conversion(markdown=markdown, blocks=contract_blocks, words=words, dropped=dropped, pages=[])


def _grounding_of(page: _Page, sources: list[tuple[int, int, int]]) -> dict:
    """One grounding for a markdown word: its single source word (or part of it), or, when the converter joined
    several source words, the range from the first one's first character to the last one's last."""
    sources = sorted(sources)
    first_index, first_start, first_end = sources[0]
    if len(sources) == 1:
        return page.grounding(first_index, first_start, first_end)
    last_index, _last_start, last_end = sources[-1]
    grounding = page.grounding(first_index, first_start, len(page.words[first_index]["text"]))
    tail = page.grounding(last_index, 0, last_end)["html"]
    grounding["html"]["source_range"]["end"] = tail["source_range"]["end"]
    exact = text_of_range(page.decoded, grounding["html"]["source_range"])
    grounding["html"]["text_quote"] = {**grounding["html"]["text_quote"], "exact": exact,
                                       "suffix": tail["text_quote"]["suffix"]}
    return grounding


def _score(markdown_text: str, source_text: str) -> float:
    markdown_normal, source_normal = normalize(markdown_text), normalize(source_text)
    if markdown_normal == source_normal:
        return 1.0
    return round(Levenshtein.normalized_similarity(markdown_normal, source_normal), 4)


def text_of_range(decoded: str, source_range: dict) -> str:
    fragment = decoded[source_range["start"]:source_range["end"]]
    return " ".join(html.unescape(_TAG_OR_COMMENT.sub("", fragment)).split())


def _grounding_from_words(words: list[dict], block_range: dict) -> list[dict]:
    """A block Defuddle built itself (no source element): the extent of its linked words."""
    ranges = [word["grounding"]["html"]["source_range"] for word in words
              if word["grounding"] and block_range["start"] <= word["range"]["start"] and word["range"]["end"] <= block_range["end"]]
    if not ranges:
        return []
    return [{"html": {"source_range": {"start": min(r["start"] for r in ranges), "end": max(r["end"] for r in ranges)},
                      "xpath": None}}]
