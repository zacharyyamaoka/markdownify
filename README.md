# Markdownify

The head of Zach's ingestion pipeline: point it at any digital asset (PDF, web page, image) and get markdown in which
every word links back to where it sits in the source. The Datasheet Extractor and RobotParts' "Go to source" build on
that link.

- **Contract:** [CONTRACT.md](CONTRACT.md) — the bundle, the span index, the coverage ledger, the KPIs, the seams.
- **Name:** Markdownify. WHY (Zach, 2026-11-16): "please STOP calling it mdify … if it overlaps this can just be an
  internal tool for me"; it is a superset of PyPI's `markdownify` (HTML → markdown only; any source → markdown here), so
  the name clash is fine. The command, the Python package and the distribution are all `markdownify`; nothing here
  depends on the PyPI library. Renamed from `mdify` on 2026-11-16: the old `mdify` command stays as a deprecated alias
  that prints a one-line pointer to `markdownify`.
- **Research:** vault `Projects/Markdownify/`.

Layout: `src/markdownify/` (converters + core), `harness/` (the round-trip ruler: markdown → render → convert → score),
`reports/` (review pages).

Where bundles go: `markdownify <path-or-url>` writes the whole bundle to `~/.cache/markdownify/bundles/<sha256>/`
(or `$MARKDOWNIFY_CACHE/bundles/…`). With `--out DIR` the bundle goes to DIR, and the cache keeps only the source
(`source.<ext>` + `source.json`). WHY: the cache's first job is to keep the exact bytes a citation points at after the
worktree that converted them is gone, while a bundle written to `--out` (a harness run, an experiment) is the
caller's. `markdownify check` refuses a source-only directory ("not a complete bundle … (source only)").

## Three pipelines: `--pipeline A|B|AB`

```
markdownify <source> [--pipeline A|B|AB] [--model claude|codex] [--out DIR] [--reader]
```

- **A (default)** — `markdownify report.pdf`: parse the source, no model. Deterministic, ~1 s/page.
- **B** — `markdownify report.pdf --pipeline B`: a vision model writes the markdown and the line boxes (one call per page, 20–50 s).
- **AB** — `markdownify report.pdf --pipeline AB`: the model writes the markdown, the source's own words (text layer, else OCR) place each word.
- `--model claude|codex` picks the logged-in CLI for B/AB (default claude); with A it is an error. `--reader` is for HTML (A).

Measured on the harness, words correctly placed: A pdf 0.982 / png 0.893; AB pdf 1.0 / png 0.999. WHY A is the default: no model call, repeatable, fast; B and AB are opt-in.

## Web capture: `markdownify capture`

```
markdownify capture <url> [--keep link,html,snapshot,pdf,single,archive] [--out DIR] [--dry-sizes]
```

One headless-Chromium visit builds every capture level (CONTRACT §2.1), prints each one's measured size, keeps the
levels you chose and deletes the rest. Then the HTML is converted as `markdownify <url>` would, and when the snapshot is
kept every word also carries its box on it (CONTRACT §3.3). WHY sizes first (Zach, 2026-10-29): "provide all the
options and then provide the estimate of the storage size, so then the user can pick."

| level | files | holds |
|---|---|---|
| `link` | `source.json` | where and when, plus the visit's request count and every level's size; always kept |
| `html` | `source.html` | the main document's body exactly as delivered (the bytes values cite) |
| `snapshot` | `snapshot.jpg` + `snapshot.json` | the full page (JPEG q80, 1280 px wide) and every visible word's box on it |
| `pdf` | `snapshot.pdf` | the browser's print of the page |
| `single` | `single.html` | the rendered page as one self-contained HTML file (SingleFile): CSS, images and fonts inlined, scripts removed, every fetch blocked; opens offline from the file alone |
| `archive` | `source.wacz` | every response of the visit as WARC records in a WACZ; replays offline (ReplayWeb.page) |

- **Default `--keep html,snapshot`.** CONTRACT §2.1's policy — `html` always, `snapshot` + `archive` for every page a
  kept value cites, `html` only for bulk scraping — is the caller's to apply: only the caller knows which pages a value
  cites. RobotParts, which stores the captured copy "for traceability", should pass `--keep html,snapshot,archive`
  for a page it cites.
- **`--dry-sizes`** visits and measures, keeps nothing.
- **`single` is a second visit.** SingleFile (`single-file-cli`, pinned in `tools/singlefile/`; install once with
  `pnpm --dir tools/singlefile install`) drives a browser of its own, so it runs only when `single` is kept or for
  `--dry-sizes`; otherwise its row says "not built". It goes through the same gate as the page (refused hosts,
  robots.txt, one request per second per host) and blocks refused hosts inside the page, but can't re-check robots.txt
  for a frame or redirect. It is a display copy: the converter keeps reading `source.html`. WHY not the converter's
  input: SingleFile inlines every image as a `data:` URI, and the converter's markdown diff is quadratic in them
  (docs/research/2026-11-05-capture-trial.md).
- **Politeness:** the page URL passes the fetcher's gate first (refused hosts, robots.txt, one request per second per
  host); every document navigation inside the visit is re-checked against robots.txt. The page's own CSS, JS and
  images load as they would for any reader. The visit's request count is logged and stored in `source.json`.
- **Where:** like `markdownify <url>`: the cache's `bundles/<sha256 of source.html>/`, or `--out DIR`.
- **Scripts:** the converter reads only the delivered HTML. When the rendered page shows far more words than the
  delivered HTML holds (a page that builds itself with JS), the capture warns; the snapshot and archive still hold
  what the scripts built.
- **Checking:** `markdownify check` also tests each word's snapshot box: the words measured under the box must contain the
  word. `markdownify.capture_replay.replay(<bundle>)` reloads the page from `source.wacz` alone, with the network blocked,
  and returns its pixel difference from `snapshot.jpg`.
- **Opening the archive:** `markdownify view <bundle>` shows a "Full archive" button when `source.wacz` was kept. It opens
  the archive in a new tab in ReplayWeb.page (2.5.4, vendored unmodified in `viewer/vendor/replaywebpage/`) at the
  captured URL and time, with no network. It is for browsing the whole capture; highlights stay on the left pane.

Measured 2026-10-29 (1280 px viewport):

| page | html | snapshot | pdf | archive | offline replay vs snapshot |
|---|---|---|---|---|---|
| CubeMars AK80-9 | 167 KB | 1.5 MB | 5.2 MB | 6.1 MB (197 responses) | same size, 0.09 % pixels changed |
| ZeroErr eRob80 | 84 KB | 1.2 MB | 6.9 MB | 7.3 MB (117 responses) | same size, 0.08 % pixels changed |
| moteus hardware reference | 45 KB | 164 KB | 95 KB | 285 KB (18 responses) | identical |

`single`, measured 2026-11-05 (opened offline: no request leaves the file; pixel difference from that day's snapshot,
which is a different visit): CubeMars 3.6 MB in 72 s (15 % — the video gallery stays in its first state), eRob80
7.6 MB in 35 s (6.7 %), moteus 160 KB in 8 s (identical).

Scoop (Harvard LIL) was trialled as the engine under the `archive` level on the same pages and not adopted: it
recorded fewer of eRob80's responses, replayed CubeMars a little worse and keeps no robots.txt gate
(docs/research/2026-11-05-capture-trial.md has the numbers).
## The viewer: `markdownify view`

```
markdownify view <source-or-bundle> [--port N] [--pipeline A|B|AB]
```

Markdownify's own page for one bundle: the source on the left (drawn by the Source Highlighter, CONTRACT §9), the
markdown on the right (rendered, or the raw `document.md`), and the KPI strip on top (time, coverage, traceability,
lost, unlinked, the representation ladder). Click a markdown word or table cell and its grounding lights up on the
source in the chosen style (highlight / box / spotlight / blackout); click the source and the markdown words there
light up. The Ledger panel lists the lost and unlinked words, each a click away from its place.

- A bundle directory opens as is; a file or URL is converted first, exactly as `markdownify <source>` does.
- It serves on 127.0.0.1 (`--port 0`, the default, takes any free port) and prints the URL. It opens no browser
  window. Ctrl-C stops it.
- Every pane boundary drags and is remembered per browser; the KPI strip and the Ledger fold.
- The page is `src/markdownify/viewer/` (plain ES modules, no build). The browser modules it needs — the Source
  Highlighter, PDF.js and the lezer markdown parser — are pinned copies in `viewer/vendor/`, each with its origin,
  version and sha256 in `VENDOR.json` (checked by `tests/test_view.py`).

Tests: `uv run pytest tests/test_view.py` (server and command); `uv run pytest tests/test_view_journey.py -s` drives
the page in headless Chromium on cached bundles and measures every link (`getBoundingClientRect` against the bundle's
own geometry, ±1 px). `uv run python tests/test_view_journey.py --media` also records the hero video and stills into
`reports/media/view/`.
