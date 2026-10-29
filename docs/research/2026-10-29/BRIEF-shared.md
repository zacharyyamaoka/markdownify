# Shared brief — "anything → markdown with a source map" (Datasheet Extractor), prior-art research 2026-10-29

## What Zach wants (his words, condensed)
A standalone ingestion project (first consumer: robot-part datasheets for robotparts.dev — actuators, PSUs, cameras).
Inputs: PDF, image, URL/HTML (later more). Two stages:
1. Turn any source into a nicely formatted **markdown** document (a table in the source → a markdown table).
2. **Every letter/word of that markdown links back to a location in the original source** (page + box for PDF/image,
   DOM range for HTML, plus fetch provenance: URL, access date, sha256). "Key and cool for traceability."
Then an LLM reads the markdown and extracts semantic values (rated torque, voltage…), citing markdown spans — and so
each value automatically links back to the raw source. A review UI shows where on the input each value came from.
He names Obsidian's **Defuddle** (kepano) as the key reference ("mix straightforward and AI OCR"). Local-first
(a skill/CLI a local agent runs), maybe hosted later.

Two pipelines he wants compared later:
- **A. Parse/OCR first:** raw text with boxes → AI looks at the page image + the raw text → writes markdown and says
  which raw text each piece links to.
- **B. AI only (VLM):** the model reads the image and writes markdown, and needs an output format/API to say *where*
  (bbox grounding).

Test idea ("FK/IK on markdown"): start from ground-truth markdown → render to HTML / PDF / PNG (forward) → run the
pipeline (inverse) → compare to the original markdown, and (since the renderer knows where every char landed) compare
the predicted source locations too.

## What already exists (don't re-research, build on it)
- robotparts.dev `tools/ingestor` v0.3.0 (2026-10-22): pdftotext -layout per page / stdlib HTML reader → logged-in
  `claude -p` CLI proposes {key, value, page, verbatim quote} → deterministic checker confirms the quote occurs on that
  page's text re-derived from the cached sha256 bytes, and binds row label → number (bind.py). Granularity = page +
  quote string; **no bboxes, no markdown, tables are flattened layout text.**
- Vault: `markdownify` skill (defuddle for web pages), marker-pdf 1.10.2 recipe on GPU (surya TableProcessor crash on
  cell-less tables), EPUB bake-off (epub2md > pandoc).
- Constraints: Ubuntu 24.04, RTX 4080 SUPER 16 GB, CUDA 12.8, Python 3.12. LLM = Zach's logged-in claude/codex CLIs
  (no API key; the Claude Agent SDK needs a metered key, so it's out). Hosted APIs are allowed as comparison points.
- Zach's rule: off-the-shelf first — find the stock part and build only the missing piece. Name exact packages,
  versions, licenses, and the exact field/function that gives provenance.

## Working answer (the hypothesis to try to BREAK)
W1. Don't build a converter: a stock layout-aware parser already emits element-level provenance (e.g. Docling's
    DoclingDocument `prov` = page + bbox + charspan; Azure Document Intelligence's `content` + `spans{offset,length}`
    + `boundingRegions` with markdown output) — we only add the char-level source map.
W2. Pipeline A (parse first, then align) beats B (VLM-only with bbox grounding) on location accuracy; VLMs win on
    reading structure. Best = hybrid: VLM/parser writes markdown, a deterministic aligner maps its chars to the
    PDF text layer / OCR word boxes.
W3. Stage 2 = grounded extraction: LLM returns verbatim spans, code aligns them to char offsets (Google LangExtract,
    Anthropic Citations, robotparts check.py) — no need for the LLM to emit coordinates.
W4. Provenance should be expressed in an existing standard (W3C Web Annotation selectors: TextQuote/TextPosition/
    Fragment(#page=,xywh=); hOCR/ALTO for OCR words) rather than a home-made format.
W5. Round-trip synthetic testing is valid and has prior art (DocBank, SynthDoG, PubLayNet, olmOCR-Bench…), but needs
    a small real labelled datasheet set too.
