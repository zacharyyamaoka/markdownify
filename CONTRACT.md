# Markdownify — contract v0 (draft, 2026-10-29)

Markdownify is the head of Zach's ingestion pipeline: point it at any digital asset and it returns markdown in which
every word links back to where it sits in the source. Everything downstream (the Datasheet Extractor, RobotParts'
"Go to source") reads this contract, never Markdownify's internals. A change here is a barrier: land it alone.

```
Raw source ──► Markdownify ──► bundle ──► Datasheet Extractor ──► values ──► RobotParts
     │                            ▲  (Option 1: bundle + raw)          │
     └────────────────────────────┴─ (Option 2: raw only) ────────────┘
                     Source Highlighter (inside Markdownify) ◄── "Go to source"
```

Research behind each choice: vault note `Projects/Markdownify/Markdownify — Anything to Markdown with a Source Map,
Prior Art (2026-10-29).md`.

## 1. Scope

- **In:** a local file or a URL. v0 media types: born-digital PDF, HTML, PNG/JPEG of a digital page.
- **Out of scope (Zach, 2026-10-29):** scans and photographed paper. Plots and diagrams are *flagged*, not converted.
- Names: the project is Markdownify; the command and Python package are `mdify`.
  WHY: PyPI's `markdownify` (an HTML→markdown library, 1.2.3) is already installed on this machine as a CLI.

## 2. The bundle

One directory per source, named by the source's sha256, under `~/.cache/markdownify/bundles/<sha256>/`
(or `$MARKDOWNIFY_CACHE`).
WHY outside git: makers' datasheets are mostly not redistributable, and a reviewer must still be able to re-check a
link against the exact bytes after the worktree that made it is gone (the same rule as robotparts' ingest cache).

| File | What it holds |
|---|---|
| `source.json` | Fetch record: `{sha256, bytes, media_type, retrievals: [{url, final_url, retrieved_at, http_status}]}` — robotparts `tools/ingestor/cache.py`'s shape, unchanged. |
| `source.<ext>` | The bytes exactly as fetched or read. Web pages are also kept as `source.warc.gz` (warcio, `WARC-Payload-Digest: sha256:…`). |
| `document.md` | The markdown. UTF-8, LF line endings. Tables are GFM tables; a table GFM cannot hold (merged cells) is an HTML `<table>` inside the markdown. |
| `index.json` | The span index (§3): where every block, table cell and word of `document.md` came from. |
| `ledger.json` | The coverage ledger (§5): the fate of every source word and every markdown word. |
| `kpis.json` | The run's KPIs (§6). |

## 3. The span index (`index.json`)

Shape copied from LandingAI ADE DPT-3's `grounding`, plus Azure Content Understanding's header-cell `kind`.
WHY not W3C Web Annotation: none of the 13 extraction tools surveyed uses it; all use "range into the markdown,
plus page and box". Web Annotation-style text quotes survive only as the HTML link-out (§3.3).

```jsonc
{
  "schema": "markdownify/index@0",
  "tool": {"name": "mdify", "version": "0.1.0", "pipeline": "A" },        // which pipeline wrote it
  "source": {"sha256": "…", "media_type": "application/pdf"},
  "markdown_sha256": "…",
  "range_units": "unicode_codepoints",                                     // ranges count code points, end exclusive
  "pages": [{"page": 1, "width": 595.28, "height": 841.89, "unit": "pt"}],
  "blocks": [ /* §3.1 */ ],
  "words":  [ /* §3.2 */ ],
  "dropped": [{"grounding": {"page": 1, "box": [0, 0, 1, 0.06]}, "reason": "header"}]   // left out on purpose (§5)
}
```

### 3.1 Blocks

```jsonc
{"id": "table-3", "type": "table", "range": {"start": 812, "end": 1490},
 "grounding": [{"page": 2, "box": [0.08, 0.31, 0.92, 0.55]}],
 "represented": true, "children": ["table_cell-41", "…"]}

{"id": "table_cell-41", "type": "table_cell", "range": {"start": 840, "end": 846},
 "row": 0, "col": 2, "rowspan": 1, "colspan": 1, "kind": "columnHeader",
 "grounding": [{"page": 2, "box": [0.61, 0.31, 0.74, 0.33]}]}
```

- `type`: `heading | paragraph | list_item | table | table_cell | caption | footnote | code | figure | plot | diagram`.
- `id` is `<type>-<n>`, unique within the bundle. The extractor cites these ids.
- `kind` (cells only): `columnHeader | rowHeader | stubHead | content`.
- `represented: false` marks a region Markdownify saw but did not convert (a torque–speed curve). It still has a
  grounding, so the ledger can count it as *flagged* and the highlighter can show it.
- `box` is `[x0, y0, x1, y1]`, each 0–1 of the page's width/height, origin top-left. A block split across pages has
  one grounding per page.

### 3.2 Words

```jsonc
{"range": {"start": 840, "end": 846}, "grounding": {"page": 2, "box": [0.612, 0.312, 0.668, 0.326]}, "score": 1.0}
```

- One row per markdown word. Markdown syntax (`#`, `|`, `**`, list bullets) is not a word and is never linked.
- `score` is the alignment score of the markdown word against the source text under its box: 1.0 = exact.
  Below 1.0 means the converter normalised or changed the text (a ligature, a model's rewrite) — flagged, never hidden.

### 3.3 HTML sources

For a web page, `grounding` is a range in the source instead of a page and box:

```jsonc
{"html": {"source_range": {"start": 40211, "end": 40217},   // code points into the decoded HTML string of source.<ext>
          "xpath": "/html/body/main/table[2]/tr[3]/td[2]",
          "text_quote": {"exact": "20 Nm", "prefix": "Rated torque ", "suffix": " Peak"}}}
```

`text_quote` builds the live-page link-out: `<url>#:~:text=Rated%20torque-,20%20Nm` (browsers highlight it natively).

## 4. Invariants (`mdify check <bundle>` re-derives each from the cached bytes; no model, no network)

1. **Faithful:** for every word, the markdown text equals the source text under its grounding (PDF: the text-layer
   characters inside the box; HTML: the text at `source_range`), after NFKC and whitespace normalisation — or its
   `score` is below 1.0 and it is listed.
2. **Traceable:** every content character of `document.md` lies inside some word's range.
3. **Accounted for:** every word of the source inventory (§5) has exactly one fate.
4. **Well formed:** word ranges never overlap; block ranges nest; every cell lies inside its table and its row/col
   fit the table.
5. **Pinned:** `source.<ext>` still hashes to `source.sha256`, and `document.md` to `markdown_sha256`.

## 5. Coverage ledger (`ledger.json`)

Two sides, counted in words (Zach, 2026-10-29: words for the ledger, cells for tables, characters only for errors
inside a matched word).

- **Source side.** The source inventory comes from outside the converter so the converter can't grade itself:
  PDF → `pdfplumber` words; HTML → the page's visible text nodes; image → ground truth (synthetic cases) or "no
  inventory" (real images, v0). Each source word has one fate:
  `linked` | `dropped` (with a reason: `navigation | header | footer | boilerplate | duplicate`) |
  `flagged` (inside a `represented: false` block, with its kind) | `lost` (none of the above).
- **Markdown side.** Each markdown word is `linked` or `unlinked` (no grounding — invented or unaligned).
- The ledger lists every `lost` and `unlinked` word with its location, so the report can show them.

## 6. KPIs (`kpis.json`)

| KPI | Definition | Target |
|---|---|---|
| Silently lost | source words with fate `lost` | 0 |
| Unlinked | markdown words with no grounding | 0 |
| Coverage | linked ÷ (source words − dropped − flagged) | 1.0 |
| Traceability | linked markdown words ÷ markdown words | 1.0 |
| Ladder | per content kind — prose, simple table, complex table (merged headers, one column per model), plot, diagram — represented ÷ seen | prose first; plots and diagrams 0 for now |
| Time | wall seconds per page, cold and warm | tracked, not optimised |

Scores that need ground truth (text edit distance, table structure score, misplaced-link rate) belong to the
round-trip harness, not to a production bundle.

## 7. Datasheet Extractor seam

The extractor is a separate project. It reads a bundle (Option 1) or only the raw source (Option 2), plus a field
list the consumer supplies — the extractor knows nothing about robot parts.

**In:** `fields.json`

```jsonc
[{"key": "rated_torque_nm", "labels": ["Rated torque", "额定扭矩"], "units": ["N·m", "Nm"],
  "kind": "number",                     // number | range | text
  "per_model": true}]                   // the value differs per model in a comparison table
```

plus an optional `model` (the part number to pick in a table with one column per model).

**Out:** `values.json`

```jsonc
{"schema": "extractor/values@0",
 "source": {"sha256": "…", "url": "…", "retrieved_at": "…"},
 "extractor": {"tool": "…", "version": "…", "model": "…", "pipeline": "option1"},
 "part": {"manufacturer": {…value…}, "model": {…value…}},
 "values": [{"key": "rated_torque_nm", "value": 20, "unit": "N·m", "raw": "20Nm", "condition": null,
             "cite": {"id": "table_cell-58"},              // Option 1: a block/cell id from index.json
             "grounding": [{"page": 2, "box": [0.61, 0.40, 0.68, 0.42]}],
             "verdict": "kept", "reasons": []}]}
```

- Option 1 cites ids; the grounding is copied from `index.json`. Code — not the model — checks `raw` is inside the
  cited cell or block, `value` is readable in `raw`, and for a table cell reads the row header (the label) and the
  column header (the model) from the index.
- Option 2 has no index, so the model must give `grounding` itself; the same checker tests it against the text layer.
- A value that fails a check is written with `verdict: "dropped"` and its reasons, never silently.

## 8. RobotParts seam

RobotParts stores, per value: value, unit, raw, the source record (url, sha256, retrieved_at) and the grounding.
How it maps onto `part.json` is RobotParts' call (its citation shape is moving on `lane/jmc-sources`; read it first).
**"Go to source":**
- on robotparts.dev (which may not host makers' files, SCHEMA2 §1.2): the maker's live URL with `#page=N` (PDF) or
  a Text Fragment built from `text_quote` (web page);
- where the cached bytes exist (Zach's machine): the Source Highlighter with the exact box.

## 9. Source Highlighter

A standalone project, `/home/bam/source-highlighter` (Zach, 2026-10-29: a stable sub-assembly anything can use):
source + locations + style (highlight, box, underline, spotlight, blackout) in → the source, highlighted, out. Its
CONTRACT.md owns the Location format; every `grounding` in this contract is one of its Locations. The Markdownify demo
(a Clank plugin, `clank-markdownify`) uses it as its left pane.
