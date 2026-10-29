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
