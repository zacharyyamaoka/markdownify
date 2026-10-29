# Markdownify

The head of Zach's ingestion pipeline: point it at any digital asset (PDF, web page, image) and get markdown in which
every word links back to where it sits in the source. The Datasheet Extractor and RobotParts' "Go to source" build on
that link.

- **Contract:** [CONTRACT.md](CONTRACT.md) — the bundle, the span index, the coverage ledger, the KPIs, the seams.
- **Command:** `mdify` (PyPI's `markdownify` is a different, already-installed library).
- **Research:** vault `Projects/Markdownify/`.

Layout: `src/mdify/` (converters + core), `harness/` (the round-trip ruler: markdown → render → convert → score),
`reports/` (review pages).

Where bundles go: `mdify <path-or-url>` writes the whole bundle to `~/.cache/markdownify/bundles/<sha256>/`
(or `$MARKDOWNIFY_CACHE/bundles/…`). With `--out DIR` the bundle goes to DIR, and the cache keeps only the source
(`source.<ext>` + `source.json`). WHY: the cache's first job is to keep the exact bytes a citation points at after the
worktree that converted them is gone, while a bundle written to `--out` (a harness run, an experiment) is the
caller's. `mdify check` refuses a source-only directory ("not a complete bundle … (source only)").

## Three pipelines: `--pipeline A|B|AB`

```
mdify <source> [--pipeline A|B|AB] [--model claude|codex] [--out DIR] [--reader]
```

- **A (default)** — `mdify report.pdf`: parse the source, no model. Deterministic, ~1 s/page.
- **B** — `mdify report.pdf --pipeline B`: a vision model writes the markdown and the line boxes (one call per page, 20–50 s).
- **AB** — `mdify report.pdf --pipeline AB`: the model writes the markdown, the source's own words (text layer, else OCR) place each word.
- `--model claude|codex` picks the logged-in CLI for B/AB (default claude); with A it is an error. `--reader` is for HTML (A).

Measured on the harness, words correctly placed: A pdf 0.982 / png 0.893; AB pdf 1.0 / png 0.999. WHY A is the default: no model call, repeatable, fast; B and AB are opt-in.

## Web capture: `mdify capture`

```
mdify capture <url> [--keep link,html,snapshot,pdf,archive] [--out DIR] [--dry-sizes]
```

One headless-Chromium visit builds every capture level (CONTRACT §2.1), prints each one's measured size, keeps the
levels you chose and deletes the rest. Then the HTML is converted as `mdify <url>` would, and when the snapshot is
kept every word also carries its box on it (CONTRACT §3.3). WHY sizes first (Zach, 2026-10-29): "provide all the
options and then provide the estimate of the storage size, so then the user can pick."

| level | files | holds |
|---|---|---|
| `link` | `source.json` | where and when, plus the visit's request count and every level's size; always kept |
| `html` | `source.html` | the main document's body exactly as delivered (the bytes values cite) |
| `snapshot` | `snapshot.jpg` + `snapshot.json` | the full page (JPEG q80, 1280 px wide) and every visible word's box on it |
| `pdf` | `snapshot.pdf` | the browser's print of the page |
| `archive` | `source.wacz` | every response of the visit as WARC records in a WACZ; replays offline (ReplayWeb.page) |

- **Default `--keep html,snapshot`.** CONTRACT §2.1's policy — `html` always, `snapshot` + `archive` for every page a
  kept value cites, `html` only for bulk scraping — is the caller's to apply: only the caller knows which pages a value
  cites. RobotParts, which stores the captured copy "for traceability", should pass `--keep html,snapshot,archive`
  for a page it cites.
- **`--dry-sizes`** visits and measures, keeps nothing.
- **Politeness:** the page URL passes the fetcher's gate first (refused hosts, robots.txt, one request per second per
  host); every document navigation inside the visit is re-checked against robots.txt. The page's own CSS, JS and
  images load as they would for any reader. The visit's request count is logged and stored in `source.json`.
- **Where:** like `mdify <url>`: the cache's `bundles/<sha256 of source.html>/`, or `--out DIR`.
- **Scripts:** the converter reads only the delivered HTML. When the rendered page shows far more words than the
  delivered HTML holds (a page that builds itself with JS), the capture warns; the snapshot and archive still hold
  what the scripts built.
- **Checking:** `mdify check` also tests each word's snapshot box: the words measured under the box must contain the
  word. `mdify.capture_replay.replay(<bundle>)` reloads the page from `source.wacz` alone, with the network blocked,
  and returns its pixel difference from `snapshot.jpg`.

Measured 2026-10-29 (1280 px viewport):

| page | html | snapshot | pdf | archive | offline replay vs snapshot |
|---|---|---|---|---|---|
| CubeMars AK80-9 | 167 KB | 1.5 MB | 5.2 MB | 6.1 MB (197 responses) | same size, 0.09 % pixels changed |
| ZeroErr eRob80 | 84 KB | 1.2 MB | 6.9 MB | 7.3 MB (117 responses) | same size, 0.08 % pixels changed |
| moteus hardware reference | 45 KB | 164 KB | 95 KB | 285 KB (18 responses) | identical |
