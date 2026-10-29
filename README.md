# Markdownify

The head of Zach's ingestion pipeline: point it at any digital asset (PDF, web page, image) and get markdown in which
every word links back to where it sits in the source. The Datasheet Extractor and RobotParts' "Go to source" build on
that link.

- **Contract:** [CONTRACT.md](CONTRACT.md) — the bundle, the span index, the coverage ledger, the KPIs, the seams.
- **Command:** `mdify` (PyPI's `markdownify` is a different, already-installed library).
- **Research:** vault `Projects/Markdownify/`.

Layout: `src/mdify/` (converters + core), `harness/` (the round-trip ruler: markdown → render → convert → score),
`reports/` (review pages).
