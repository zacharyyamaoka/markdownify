"""The markdown → source word aligner: which source word each word of the markdown came from, and how exactly.

Every converter that writes its own markdown (pipeline A: docling items, HTML nodes) or receives it from a model
(pipeline B) ends up with the same problem: a string of markdown on one side, the source's own words (with their
boxes or ranges) on the other, and no map between them. No open-source converter keeps that map below block level
(research 1-converters.md, W1), so this module builds it, the same way for every converter:

1. `markdown_words` cuts the markdown into content words. Markdown syntax (`#`, list bullets, table pipes and
   separator rows, `**`, backticks, link URLs, raw-HTML tags) is never a word, so it is never linked (CONTRACT §3.2).
2. `align_word_spans` aligns those words to the source words: a word-level sequence alignment (rapidfuzz
   opcodes) over normalised words, then a character-level alignment inside every stretch that did not match
   word for word — so a word the converter split ("10A" → "10", "A") or joined still finds its source.
   It runs block by block when the caller knows which source words a block covers (docling's item boxes, an HTML
   node's text), so a reordered block — two columns read in another order — cannot derail the rest.

Scores: 1.0 means the normalised markdown word equals the normalised source text it points at. Below 1.0 means the
converter changed something (a model's rewrite, a dropped digit) — it is still linked, and flagged, never hidden.
Below `min_score` the word is left unlinked rather than pinned to a guess.

Normalisation is NFKC (which also expands the ﬁ/ﬂ ligatures) with soft hyphens and zero-width characters removed,
character by character so every normalised character still knows which source character it came from — that is
what lets a PDF converter box exactly the characters a split word covers.
"""

from __future__ import annotations

import dataclasses
import functools
import html
import re
import unicodedata

from rapidfuzz.distance import Indel, Levenshtein

# Characters that carry no text: removed before comparing, on both sides.
_INVISIBLE = {"­", "​", "‌", "‍", "⁠", "﻿"}

# --- the content mask -------------------------------------------------------------------------------------------
# Each code point of the markdown is one of:
CONTENT, SYNTAX, BREAK = 0, 1, 2
# CONTENT is text a reader reads; SYNTAX is markdown that is invisible but does not split a word (`**`, a backslash
# escape, backticks); BREAK splits words like whitespace does (table pipes, a raw-HTML tag, a link's URL).

_ASCII_PUNCTUATION = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_THEMATIC_BREAK = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?=\s|$)")
_QUOTE = re.compile(r"^\s{0,3}(>\s?)+")
_BULLET = re.compile(r"^\s*([-*+]|\d{1,9}[.)])(?=\s)")
_TAG = re.compile(r"<!--.*?-->|</?[A-Za-z][A-Za-z0-9-]*(\s+[^<>]*)?/?>", re.S)
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_CALLOUT = re.compile(r"^\s*\[![A-Za-z][\w-]*\][+-]?(?=\s|$)")       # Obsidian / GitHub alert: > [!NOTE]
_FOOTNOTE_DEFINITION = re.compile(r"^\s{0,3}\[\^[^\]\s]+\]:")
_FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]\s]+\]")
_LINK_TAIL = re.compile(r"\]\(([^()\s]*|<[^>\n]*>)(\s+\"[^\"\n]*\")?\)")


@dataclasses.dataclass(frozen=True)
class MarkdownWord:
    start: int      # code point offset into the markdown, end exclusive
    end: int
    text: str       # the content characters only: escapes and emphasis removed, HTML entities decoded


@functools.lru_cache(maxsize=4)
def content_mask(markdown: str) -> bytes:
    """CONTENT / SYNTAX / BREAK for every code point of `markdown`. Cached: the checker asks once per word."""
    mask = bytearray(len(markdown))  # all CONTENT (0)
    for position, character in enumerate(markdown):
        if character.isspace():
            mask[position] = BREAK

    escaped = _escaped_positions(markdown)

    def mark(start: int, end: int, kind: int) -> None:
        for position in range(start, end):
            if not markdown[position].isspace():
                mask[position] = kind

    # Raw HTML tags and comments split words (a <td> boundary is a cell boundary); images are not source text.
    for match in _TAG.finditer(markdown):
        if match.start() not in escaped:
            mark(match.start(), match.end(), BREAK)
    for match in _IMAGE.finditer(markdown):
        if match.start() not in escaped:
            mark(match.start(), match.end(), BREAK)
    for match in _FOOTNOTE_REFERENCE.finditer(markdown):
        if match.start() not in escaped:
            mark(match.start(), match.end(), BREAK)

    in_fence = False
    offset = 0
    for line in markdown.split("\n"):
        end = offset + len(line)
        if _FENCE.match(line):
            in_fence = not in_fence
            mark(offset, end, BREAK)
        elif in_fence:
            pass  # code is content as written, backslashes and all
        elif "|" in line and "-" in line and _TABLE_SEPARATOR.match(line):
            mark(offset, end, BREAK)
        elif _THEMATIC_BREAK.match(line):
            mark(offset, end, BREAK)
        else:
            _mark_line(markdown, offset, line, mask, escaped)
        offset = end + 1
    return bytes(mask)


def _escaped_positions(markdown: str) -> set[int]:
    """Positions of characters made literal by a backslash (the backslash itself is SYNTAX)."""
    escaped: set[int] = set()
    position = 0
    while position < len(markdown) - 1:
        if markdown[position] == "\\" and markdown[position + 1] in _ASCII_PUNCTUATION:
            escaped.add(position + 1)
            position += 2
        else:
            position += 1
    return escaped


def _mark_line(markdown: str, offset: int, line: str, mask: bytearray, escaped: set[int]) -> None:
    prefix_end = 0
    progressed = True
    while progressed:  # "> - ## Title": a quote, a bullet and a heading, in any nesting
        progressed = False
        for pattern in (_QUOTE, _BULLET, _HEADING, _CALLOUT, _FOOTNOTE_DEFINITION):
            match = pattern.match(line[prefix_end:])
            if match and match.end():
                prefix_end += match.end()
                progressed = True
    for position in range(offset, offset + prefix_end):
        if not markdown[position].isspace():
            mask[position] = BREAK

    is_table_row = line.lstrip().startswith("|")
    for match in _LINK_TAIL.finditer(line):
        start = offset + match.start()
        if start in escaped or mask[start] == BREAK:
            continue
        paragraph_start = markdown.rfind("\n\n", 0, start) + 1
        opener = _matching_bracket(markdown, paragraph_start, start, escaped)
        if opener is None:
            continue
        mask[opener] = SYNTAX
        for position in range(start, offset + match.end()):
            mask[position] = BREAK

    for index in range(prefix_end, len(line)):
        position = offset + index
        character = line[index]
        if mask[position] != CONTENT or position in escaped:
            continue
        if character == "\\" and position + 1 in escaped:
            mask[position] = SYNTAX
        elif character == "|" and is_table_row:
            mask[position] = BREAK
        elif character in "*`":
            mask[position] = SYNTAX
        elif character == "~" and (line[index + 1:index + 2] == "~" or line[index - 1:index] == "~"):
            mask[position] = SYNTAX
        elif character == "_":
            # CommonMark: an underscore inside a word (snake_case) is text; at a word's edge it is emphasis.
            before = line[index - 1] if index > 0 else " "
            after = line[index + 1] if index + 1 < len(line) else " "
            if not (before.isalnum() and after.isalnum()):
                mask[position] = SYNTAX


def _matching_bracket(markdown: str, line_start: int, close: int, escaped: set[int]) -> int | None:
    depth = 0
    for position in range(close - 1, line_start - 1, -1):
        if position in escaped:
            continue
        if markdown[position] == "]":
            depth += 1
        elif markdown[position] == "[":
            if depth == 0:
                return position
            depth -= 1
    return None


def markdown_words(markdown: str) -> list[MarkdownWord]:
    """The content words of `markdown`, in order. A word runs from its first to its last content character
    between two BREAKs; SYNTAX inside it (`a\\*b`, `**bold**`) stays inside the range but out of its text."""
    mask = content_mask(markdown)
    words: list[MarkdownWord] = []
    start = None
    last_content = None
    for position in range(len(markdown) + 1):
        kind = mask[position] if position < len(markdown) else BREAK
        if kind == BREAK:
            if start is not None:
                words.append(_word(markdown, mask, start, last_content + 1))
            start = last_content = None
        elif kind == CONTENT:
            if start is None:
                start = position
            last_content = position
    return words


def _word(markdown: str, mask: bytes, start: int, end: int) -> MarkdownWord:
    return MarkdownWord(start, end, word_text(markdown, start, end, mask))


_ENTITY = re.compile(r"&(#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")


def word_text(markdown: str, start: int, end: int, mask: bytes | None = None) -> str:
    """The content characters of markdown[start:end], with HTML entities decoded (`&amp;` in a raw-HTML cell is
    the source's `&`) unless their `&` was escaped (`\\&amp;` is the literal text "&amp;")."""
    mask = mask if mask is not None else content_mask(markdown)
    raw = "".join(markdown[position] for position in range(start, end) if mask[position] == CONTENT)
    if "&" not in raw:
        return raw
    segment = markdown[start:end]
    pieces = []
    position = start
    for match in _ENTITY.finditer(segment):
        absolute = start + match.start()
        if absolute > 0 and markdown[absolute - 1] == "\\" and mask[absolute - 1] == SYNTAX:
            continue  # an escaped ampersand: the entity is literal text
        pieces.append(_content(markdown, mask, position, absolute))
        pieces.append(html.unescape(match.group(0)))
        position = start + match.end()
    pieces.append(_content(markdown, mask, position, end))
    return "".join(pieces)


def _content(markdown: str, mask: bytes, start: int, end: int) -> str:
    return "".join(markdown[position] for position in range(start, end) if mask[position] == CONTENT)


def word_ranges_of(markdown: str) -> list[tuple[int, int]]:
    """Code-point ranges (end exclusive) of the content words of `markdown` (CONTRACT §3.2)."""
    return [(word.start, word.end) for word in markdown_words(markdown)]


# --- normalisation ----------------------------------------------------------------------------------------------
def normalize_with_origin(text: str) -> tuple[str, list[int]]:
    """NFKC character by character, invisible characters removed; origin[k] is the index in `text` of normalised
    character k. Per character rather than per string so the origin map exists (they differ only for combining
    sequences, which datasheets do not use)."""
    pieces: list[str] = []
    origin: list[int] = []
    for index, character in enumerate(text):
        if character in _INVISIBLE:
            continue
        normal = unicodedata.normalize("NFKC", character)
        for piece in normal:
            if piece.isspace() or piece in _INVISIBLE:
                continue
            pieces.append(piece)
            origin.append(index)
    return "".join(pieces), origin


def normalize(text: str) -> str:
    return normalize_with_origin(text)[0]


# --- alignment --------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class WordAlignment:
    # (source word index, start, end): the characters of that source word's ORIGINAL text this markdown word
    # covers, end exclusive. One entry normally; several when the converter joined source words.
    sources: list[tuple[int, int, int]]
    score: float

    @property
    def index(self) -> int | None:
        return self.sources[0][0] if self.sources else None


UNALIGNED = WordAlignment([], 0.0)
# WHY a cap: rapidfuzz's character alignment keeps an N×M/64 word bit matrix for the traceback; past this many
# cells a stretch is matched word by word instead (exact text only).
_MAX_CHARACTER_CELLS = 400_000_000


def align_word_spans(markdown_texts: list[str], source_texts: list[str], *,
                     groups: list[tuple[list[int], list[int]]] | None = None,
                     min_score: float = 0.5, residual: bool = True) -> list[WordAlignment]:
    """Align markdown words to source words (both as lists of their text).

    `groups` = [(markdown word indices, source word indices)], one per block whose source words are known: each is
    aligned on its own, in order, and source characters claimed by one group are not offered to the next. Whatever
    is still unaligned afterwards is aligned once more against the characters nobody claimed, so a block whose box
    missed a word still finds it. Pass `residual=False` to skip that global pass (a converter that adds its own
    per-page leftover groups, so a word never finds a twin on another page).
    """
    results: list[WordAlignment] = [UNALIGNED] * len(markdown_texts)
    markdown_normal = [normalize_with_origin(text) for text in markdown_texts]
    source_normal = [normalize_with_origin(text) for text in source_texts]
    # Claims are per character, not per word. WHY: a source word can hold two markdown words' text (pdfplumber glues
    # neighbouring table cells, "LRS-350-4.2LRS-350-5"); the cell that takes the first half must leave the second
    # half to the next cell.
    claimed: dict[int, set[int]] = {}

    def unclaimed(j: int) -> tuple[str, list[int]]:
        text, origin = source_normal[j]
        taken = claimed.get(j)
        if not taken:
            return text, origin
        kept = [(character, at) for character, at in zip(text, origin) if at not in taken]
        return "".join(character for character, _at in kept), [at for _character, at in kept]

    def run(markdown_indices: list[int], source_indices: list[int]) -> None:
        markdown_indices = [i for i in markdown_indices if results[i] is UNALIGNED and markdown_normal[i][0]]
        view = {j: unclaimed(j) for j in source_indices}
        source_indices = [j for j in source_indices if view[j][0]]
        if not markdown_indices or not source_indices:
            return
        for i, alignment in _align(markdown_indices, source_indices, markdown_normal, view, min_score):
            results[i] = alignment
            for source, start, end in alignment.sources:
                claimed.setdefault(source, set()).update(range(start, end))

    for markdown_indices, source_indices in groups or []:
        run(list(markdown_indices), list(source_indices))
    if residual:
        run(list(range(len(markdown_texts))), list(range(len(source_texts))))
    return results


def align_words(markdown: str, word_ranges: list[tuple[int, int]], source_words: list, *,
                block_spans: list[tuple[int, int, list[int]]] | None = None,
                min_score: float = 0.5) -> list[tuple[int | None, float]]:
    """For each markdown word (by its range), its source word index or None, and a score from 1.0 (exact) to 0.

    `source_words` are strings or objects with a `.text` (SourceWord). `block_spans` = [(markdown start, markdown
    end, source word indices)] for blocks whose source words are known.
    """
    mask = content_mask(markdown)
    markdown_texts = [word_text(markdown, start, end, mask) for start, end in word_ranges]
    source_texts = [word if isinstance(word, str) else word.text for word in source_words]
    groups = None
    if block_spans:
        groups = []
        for block_start, block_end, source_indices in block_spans:
            inside = [i for i, (start, end) in enumerate(word_ranges) if start >= block_start and end <= block_end]
            groups.append((inside, list(source_indices)))
    alignments = align_word_spans(markdown_texts, source_texts, groups=groups, min_score=min_score)
    return [(alignment.index, alignment.score) for alignment in alignments]


def _align(markdown_indices, source_indices, markdown_normal, source_normal, min_score):
    """Word-level opcodes, then character-level alignment inside each run of non-equal opcodes.

    WHY Indel (insert/delete only) rather than Levenshtein for the opcodes: it maximises the number of characters
    or words that match exactly (a longest common subsequence), so an inserted word never shifts its neighbours
    into a substitution; a changed word is still found by its matching characters and scored below 1.0.
    """
    left = [markdown_normal[i][0] for i in markdown_indices]
    right = [source_normal[j][0] for j in source_indices]
    pending: tuple[int, int, int, int] | None = None  # a stretch of non-equal opcodes: (i1, i2, j1, j2)
    for opcode in Indel.opcodes(left, right):
        if opcode.tag == "equal":
            if pending:
                yield from _align_stretch(pending, markdown_indices, source_indices, markdown_normal, source_normal,
                                          min_score)
                pending = None
            for offset in range(opcode.src_end - opcode.src_start):
                i = markdown_indices[opcode.src_start + offset]
                j = source_indices[opcode.dest_start + offset]
                origin = source_normal[j][1]
                yield i, WordAlignment([(j, origin[0], origin[-1] + 1)], 1.0)
        else:
            if pending is None:
                pending = (opcode.src_start, opcode.src_end, opcode.dest_start, opcode.dest_end)
            else:
                pending = (pending[0], opcode.src_end, pending[2], opcode.dest_end)
    if pending:
        yield from _align_stretch(pending, markdown_indices, source_indices, markdown_normal, source_normal, min_score)


def _align_stretch(stretch, markdown_indices, source_indices, markdown_normal, source_normal, min_score):
    i1, i2, j1, j2 = stretch
    if i1 == i2 or j1 == j2:
        return
    stretch_markdown = markdown_indices[i1:i2]
    stretch_source = source_indices[j1:j2]
    left = "".join(markdown_normal[i][0] for i in stretch_markdown)
    right = "".join(source_normal[j][0] for j in stretch_source)
    if len(left) * len(right) > _MAX_CHARACTER_CELLS:
        yield from _align_exact(stretch_markdown, stretch_source, markdown_normal, source_normal)
        return
    # Who owns each character of the two concatenations: (word index, offset in its normalised text).
    left_owner = [(i, k) for i in stretch_markdown for k in range(len(markdown_normal[i][0]))]
    right_owner = [(j, k) for j in stretch_source for k in range(len(source_normal[j][0]))]
    paired: dict[int, list[tuple[int, int]]] = {}   # markdown word → [(source word, normalised offset)]
    for opcode in Indel.opcodes(left, right):
        if opcode.tag != "equal":
            continue
        for offset in range(min(opcode.src_end - opcode.src_start, opcode.dest_end - opcode.dest_start)):
            i, _k = left_owner[opcode.src_start + offset]
            paired.setdefault(i, []).append(right_owner[opcode.dest_start + offset])
    for i in stretch_markdown:
        pairs = paired.get(i)
        if not pairs:
            continue
        spans: dict[int, list[int]] = {}
        for j, k in pairs:
            spans.setdefault(j, []).append(k)
        if len(spans) > 1 and not _contiguous(spans, stretch_source, source_normal):
            # WHY: letters picked from unrelated words ("M" of "Maximum" + "ode" of "Model") are not the source
            # saying "Mode". Keep only the word that gave the most letters, scored against what it really says.
            best = max(spans, key=lambda j: len(spans[j]))
            spans = {best: spans[best]}
        covered = "".join(source_normal[j][0][min(ks):max(ks) + 1] for j, ks in spans.items())
        score = Levenshtein.normalized_similarity(markdown_normal[i][0], covered)
        if score < min_score:
            continue
        sources = []
        for j, ks in spans.items():
            origin = source_normal[j][1]
            sources.append((j, origin[min(ks)], origin[max(ks)] + 1))
        yield i, WordAlignment(sources, round(score, 4))


def _contiguous(spans: dict[int, list[int]], stretch_source: list[int], source_normal) -> bool:
    """True when the covered characters run unbroken across neighbouring source words: the first word to its end,
    every middle word whole, the last word from its start — a word the converter joined ("N·m" from "N", "·", "m")."""
    position = {j: n for n, j in enumerate(stretch_source)}
    ordered = sorted(spans, key=position.__getitem__)
    if [position[j] for j in ordered] != list(range(position[ordered[0]], position[ordered[0]] + len(ordered))):
        return False
    for n, j in enumerate(ordered):
        ks = sorted(spans[j])
        last = len(source_normal[j][0]) - 1
        if ks != list(range(ks[0], ks[-1] + 1)):
            return False
        if n > 0 and ks[0] != 0:
            return False
        if n < len(ordered) - 1 and ks[-1] != last:
            return False
    return True


def _align_exact(stretch_markdown, stretch_source, markdown_normal, source_normal):
    """Fallback for a stretch too large to align by character: each markdown word takes the next unused source
    word with exactly its normalised text."""
    unused: dict[str, list[int]] = {}
    for j in stretch_source:
        unused.setdefault(source_normal[j][0], []).append(j)
    for i in stretch_markdown:
        candidates = unused.get(markdown_normal[i][0])
        if candidates:
            j = candidates.pop(0)
            origin = source_normal[j][1]
            yield i, WordAlignment([(j, origin[0], origin[-1] + 1)], 1.0)
