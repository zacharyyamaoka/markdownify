# 3 — Evaluation: the "FK/IK on markdown" round-trip harness (Markdownify)

Researcher 3 of 3 · desk research + one 20-line local probe · 2026-10-29 · all web sources accessed 2026-10-29.

Terms I coin (defined once):
- **forward render** = ground-truth markdown → HTML / PDF / PNG. **inverse** = the extraction pipeline, which goes back to markdown plus links.
- **link** = a claim by the pipeline that a markdown span `[start, end)` came from a given source location (page + box, or DOM range).
- **truth table** = the renderer's own record of where each markdown character landed: `md_offset → page, box`.
- **ledger** = a per-word account of every source word's fate and every markdown word's origin (the coordinator's KPI proposal, see Metrics §d).
- **ruler** = the scoring code plus truth data. "Freeze the ruler" means pipelines can't change it.

---

## Verdict

- **W5 → SHARPENED.** Round-trip testing is valid and cheap, but the brief's prior-art list mixes up three different things:
  - **Alignment:** DocBank, PubLayNet, PubTabNet, Nougat. They align an existing source to an existing PDF.
  - **Rendered synthetic:** READoc-GitHub, olmOCR-synthmix, GOT, Infinity-Parser2. These are the actual round trips.
  - **Human-verified real:** olmOCR-Bench, OmniDocBench, ParseBench, SCORE-Bench.
  - Only READoc-GitHub is literally *markdown in → PDF out* (Pandoc + Eisvogel) [9](https://arxiv.org/html/2409.05137v3).
  - **Nobody publishes markdown-offset → page-box ground truth. That piece is new, but it is small.**
- **Clean synthetic renders overestimate accuracy, by a lot.**
  - READoc, on markdown-rendered GitHub pages vs arXiv vs Zenodo: Marker 81.0 / 65.0 / 46.4 and GPT-4o-mini 79.5 / 58.0 / 55.6 [9](https://arxiv.org/html/2409.05137v3).
  - DocLayNet: a PubLayNet-trained (auto-labelled) model scores 93 mAP in-domain but 30 on real diverse layouts [11](https://arxiv.org/abs/2206.01062).
  - olmOCR 2: 99.7 on the "base" category vs 47.7 on old scans [1](https://arxiv.org/html/2510.19817v1).
- **The trap specific to our design: a Chrome-printed PDF has a perfect text layer.**
  - I measured all 166 of 166 characters matching, with ≤ 0.014 pt horizontal error against the DOM.
  - So Pipeline A (parse first) gets near-perfect text and boxes *by construction*, and the harness would "confirm" W2 by construction too.
  - The image-only and degraded arms are therefore **mandatory, not optional**. A real-datasheet tier is mandatory as well.
- **Markdown can't be the whole ground-truth source for datasheets.**
  - GFM pipe tables have no merged cells. olmOCR-Bench notes that only HTML tables can reach a perfect score on rowspan/colspan tests [2](https://github.com/allenai/olmocr/tree/main/olmocr/bench).
  - The source should be markdown with raw-HTML tables plus SVG plots with known data.
  - Score with structure metrics and unit tests, not an exact text diff, because many outputs are equally valid.
- **The ground-truth chain has a measured least-code path: `pandoc -f commonmark_x+sourcepos` → headless Chrome → `Range.getClientRects()` per character.**
  - Pandoc 3.1.3 tags *every token* with `data-pos="line:col-line:col"`.
  - Two gotchas, both measured:
    - Turn ligatures off, or Chrome emits ﬀ/ﬁ/ﬂ/ﬄ as one glyph covering 2–3 characters.
    - DOM boxes and PDF text-layer boxes use different vertical conventions. A *perfect* answer then scores only IoU ≈ 0.78, so gate on asymmetric overlap (IoA, ParseBench's rule), not raw IoU.
- **On the coordinator's coverage ledger:** prior art has every half, but nobody has the whole.
  - Source-side recall = ASR "deletions" = SCORE's "Percent Tokens Found" = ParseBench's omission rules.
  - Output-side invention = "insertions" = "Percent Tokens Added".
  - Grounding rate is closest to ParseBench's LAP/LAR attribution metrics and ALCE's citation precision/recall.
  - Drop-with-reason = ICDAR "do-not-care" regions and olmOCR "absence" tests.
  - **No tool keeps a per-word fate-with-reason ledger, or runs it ground-truth-free as an invariant.** Unit: word for the ledger, a separate digit gate, cells for tables. Characters only as the error inside a matched word.

---

## Prior-art table

"Round trip?" means it starts from a source, renders it, and keeps exact labels. Boxes are what the labels carry.

| Dataset / tool | How exact labels were obtained | Round trip? | Boxes in GT | Markdown/structure GT | Use for us |
|---|---|---|---|---|---|
| **DocBank** (2020, 500K pages) [10](https://arxiv.org/abs/2006.01038) | Recolour each LaTeX structure with `\color`, recompile, read tokens + RGB with PDFPlumber/PDFMiner; colour → label | Partial (source edited, re-rendered) | **Token boxes** (x0,y0,x1,y1) | Semantic class per token, no markdown | The colour trick is the image-only way to get token boxes. Chrome DOM rects make it unnecessary for us |
| **PubLayNet / PubTabNet** (2019) [12](https://arxiv.org/abs/1911.10683) | Match PubMed XML nodes to the PDF; verify by extracting the PDF text inside each box and comparing | No (alignment) | Block / table boxes; cell text boxes (updated version) | Table HTML | Its verify-by-re-extraction check is how we self-check the truth table. Also origin of TEDS |
| **FinTabNet** [12](https://arxiv.org/abs/1911.10683) | Auto-label table/cell structure in S&P 500 report PDFs | No | Cell boxes (text part only) | Table HTML | Real financial-report tables. TableFormer notes residual text mismatches (spacing, Unicode forms) [15](https://arxiv.org/abs/2203.01017) |
| **PubTables-1M / GriTS** [13](https://arxiv.org/abs/2110.00061) [14](https://arxiv.org/abs/2203.12555) | PMC XML ↔ PDF with canonicalisation | No | Cell boxes | Grid | GriTS metric (matrix-based) |
| **Nougat** data [17](https://arxiv.org/abs/2308.13418) | arXiv LaTeX → LaTeXML HTML → markup, split at page breaks via bag-of-words + fuzzy match to MuPDF text. Keeps only ~47% of pages | No (alignment) | None | Page markdown | Shows how lossy alignment is: half the pages are discarded |
| **SynthDoG** (Donut) [16](https://arxiv.org/abs/2111.15664) | SynthTIGER: Wikipedia text pasted on ImageNet/paper textures, rule-based layout | Yes (text → image) | Generator knows them; not shipped as GT | Text only, no tables | Degradation ideas only |
| **GOT** [18](https://arxiv.org/abs/2409.01704) | Render sources: Mathpix-markdown-it via Chrome driver (math), LaTeX (tables), TikZ, Verovio, Matplotlib/Pyecharts (charts) | **Yes** | No | Markup | Precedent for "Chrome renders markdown-ish source". Charts from Matplotlib give known data |
| **READoc** (ACL Findings 2025) [9](https://arxiv.org/html/2409.05137v3) | GitHub READMEs: **markdown → PDF via Pandoc + Eisvogel**. Zenodo: HTML → Pandoc → **PDF via Chromium**. Keeps only error-free renders | **Yes, literally markdown → PDF** | **None** | Markdown | Closest analogue to Zach's idea; no locations. Its own numbers show the realism gap |
| **olmOCR-synthmix-1025** [1](https://arxiv.org/html/2510.19817v1) [4](https://github.com/allenai/olmocr/tree/main/olmocr/synth) | Real page → Claude Sonnet 4 writes semantic HTML (3 passes) → Playwright/Chromium renders → tests mined from HTML structure. 2,186 pages, 30,381 tests, ~$0.12/page | **Yes (HTML → PDF/PNG)** | None scored (math compares relative KaTeX symbol boxes) | Markdown targets + unit tests | **The harness to copy**, minus locations. `mine_html_templates.py`, `augmentations.py` |
| **Infinity-Parser / -2** [21](https://arxiv.org/abs/2607.07836) | Infinity-Doc-55K/400K synthetic scans; v2 (Jul 2026) adds a "controllable rendering framework" → 5M samples **with bounding boxes**, content forms and reading order | Yes | **Yes (block level)** | MD / HTML / LaTeX | Possibly copyable generator ("Infinity-Synth"). I could not locate or verify the repo |
| **WordScape** (NeurIPS 2023) [19](https://arxiv.org/abs/2312.10188) | Word Open XML → render → box labelling algorithm from the XML | Yes (docx → image) | Block boxes | Reading-order text, tables | Docling later dropped its table labels as mislabelled. Source-derived labels are not automatically right |
| **DocSynth-300K** [20](https://arxiv.org/abs/2410.12628) | Bin-pack real element crops into synthetic layouts | Yes | Block boxes | Layout classes | Layout diversity, not text |
| **olmOCR-Bench** [2](https://github.com/allenai/olmocr/tree/main/olmocr/bench) [3](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py) | Real PDFs; tests drafted by Gemini/Claude, then human-reviewed. 1,402 PDFs, 7,010 tests | No | No | Unit tests | **Test schema to copy** |
| **OmniDocBench** v1.7 (2026-04) [7](https://github.com/opendatalab/OmniDocBench) | Human-annotated real pages, 1,355+ pages | No | Block boxes (scored only in a separate layout config) | Markdown blocks | Metric code: NED, TEDS, CDM, reading order; custom GT supported |
| **ParseBench** (Apr 2026) [22](https://arxiv.org/html/2604.08538v3) | ~2,000 enterprise pages; VLM auto-label pass 1, human verify pass 2 | No | **Yes, block-level grounding scored** | Rules + tables + charts | **The only parsing benchmark that scores grounding.** Copy its IoA rule, TableRecordMatch, digit bag |
| **SCORE-Bench** (Dec 2025) [23](https://mta-sts.unstructured.io/blog/introducing-score-bench-an-open-benchmark-for-document-parsing) | Expert-annotated real docs | No | No | Tokens, tables | "Percent Tokens Found / Added" formulas |
| **CC-OCR V2** (May 2026) [27](https://arxiv.org/abs/2605.03903) · **OCRBench v2** [26](https://arxiv.org/abs/2501.00321) | Real images; text → box queries | No | Single target box; IoU, Acc@0.5 | — | Grounding is the weakest track (many models score 2–10%) |
| **GutenOCR** (Jan 2026) [28](https://arxiv.org/abs/2601.14490) · **PreciseDoc** (Jun 2026) [29](https://arxiv.org/abs/2606.24118) | Grounded-OCR VLMs trained partly on "synthetic grounding data" (PreciseDoc: generated documents with coordinate metadata) | Yes (for training) | Line/paragraph boxes (GutenOCR) | Text | Evidence that VLM grounding is trainable from synthetic boxes. GutenOCR's composite grounded-OCR score went 0.40 → 0.82; its benchmark is not public |
| **docling-eval** (MIT) [24](https://github.com/docling-project/docling-eval) | Wraps DP-Bench, OmniDocBench, DocLayNet, FinTabNet, … `create`/`create-gt`/`evaluate`/`visualize` CLI | No | Word-level OCR matching (`ocr_evaluator.py`); block IoU → BLEU/F1 (`bbox_text_evaluator.py`) | Markdown text, tables | The closest stock **word-box × text** scorer. Its bbox_text matcher has *no IoU threshold* (any IoU wins), so don't use it as a gate |

**Is there an open "markdown → render → parse → diff" harness to copy exactly?**
- No single one does all four steps *with locations*.
- olmOCR's synth + bench pair is closest: HTML → Chromium → PDF/PNG → any parser → unit tests. Its `augmentations.py` has only JPEG re-compression (quality 70–95), typo injection and "densify" [5](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/synth/augmentations.py). There is no blur/noise/scan model, so add Augraphy.
- READoc's Evaluation S3uite (Standardise → Segment → Score) is the closest *markdown-diff* scorer [9](https://arxiv.org/html/2409.05137v3).
- Marker's current `benchmarks/` just runs olmOCR-Bench. It has no round trip.

---

## Metrics

### (a) Markdown text fidelity
- **Normalized edit distance (NED)** on normalised text, as in OmniDocBench (lower is better) [7](https://github.com/opendatalab/OmniDocBench).
  - Normalise first using olmOCR's `normalize_text`: `<br>` → space, bold/italic stripped, whitespace collapsed, NFC, quote/dash/minus/micro-sign folding [3](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py).
  - Add NFKC so ﬁ becomes fi.
- **The S/D/I split.** Report the edit as Substitutions / Deletions / Insertions (jiwer `process_words`) [34](https://github.com/jitsi/jiwer), not just one number. Deletions are omissions; insertions are hallucinations. CER/WER averaging hides these failure modes, as LlamaIndex and SCORE both argue [23](https://mta-sts.unstructured.io/blog/introducing-score-bench-an-open-benchmark-for-document-parsing).
- **Unit tests** in olmOCR-Bench schema, auto-mined from the source:
  - `present`, `absent` (intended drops such as `<header>`/`<footer>`), `order` (before/after), `table` (cell + up/down/left/right/top_heading/left_heading), `footnote` (marker + text before/after), `baseline` (repetition).
  - Fuzzy matching via rapidfuzz `partial_ratio` / fuzzysearch `find_near_matches`, with a `max_diffs` budget [3](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py).
  - Unit tests are robust to the many valid markdowns per document. NED is not.
  - Known weaknesses: order tests only check local pairs, and verbatim-substring presence tests are brittle [6](https://www.llamaindex.ai/blog/olmocr-bench-review-insights-and-pitfalls-on-an-ocr-benchmark). Pair them with NED rather than replacing it.
- **A digit gate** (datasheets live in numbers): ParseBench's `bag_of_digit_percent` compares digit frequency distributions [22](https://arxiv.org/html/2604.08538v3).
  - The paper admits it misses swaps between equally frequent digits.
  - So also run exact-match on every *numeric token* in its context (row label + header).

### (b) Table fidelity
- **TEDS and TEDS-S** (structure only) from PubTabNet's `src/metric.py` (APTED tree edit). It is also vendored in OmniDocBench and marker [12](https://arxiv.org/abs/1911.10683).
  - It needs HTML tables on both sides; convert pipe tables first.
  - READoc converts markdown tables to LaTeX for the same reason: only that can express merged cells [9](https://arxiv.org/html/2409.05137v3).
- **GriTS-Con / GriTS-Top** (microsoft/table-transformer `src/grits.py`). It treats a table as a matrix and gives an F-score; it suits grid-thinking better than a tree [14](https://arxiv.org/abs/2203.12555).
- **TableRecordMatch** (ParseBench): each row is a record keyed by column headers, records are optimally matched, and dropped or transposed headers are penalised. GTRM = mean of GriTS and TRM [22](https://arxiv.org/html/2604.08538v3).
- **Datasheet adaptation (mine):** a multi-model table has one column per model, so its natural record is a *column* keyed by row label ("Rated torque (N·m)").
  - Score TRM on the table *and* its transpose, and take the max.
  - A **header-path check** per numeric cell catches "value bound to the wrong model". This is the failure that matters most for robotparts. The path is model column + row label + unit carried in the header.

### (c) Location accuracy
- **Nothing public scores character- or word-level links from markdown back to the source.** The closest are:
  - **ParseBench Element Pass Rate** is block-level [22](https://arxiv.org/html/2604.08538v3).
    - Localised if IoA(GT,Pred) ≥ 0.50 **and** IoA(Pred,GT) ≥ 0.20, where IoA = intersection over the *first* box's area.
    - Attribution passes if token F1 ≥ 0.80 against the text in that region.
    - Diagnostics: LAP/LAR (local attribution precision/recall).
    - Result: parsers reach 66–81% (Azure DI 73.8, Textract 70.4, Docling 66.1); general VLMs score < 10% (GPT-5 Mini 6.2, Haiku 4.5 6.7).
  - **OCRBench v2 / CC-OCR V2**: one box per text query, mean IoU and Acc@0.5 [26](https://arxiv.org/abs/2501.00321) [27](https://arxiv.org/abs/2605.03903).
  - **docling-eval `ocr_evaluator.py`**: word-level by default. It position-matches words, then reports word/char accuracy plus precision/recall/F1 [24](https://github.com/docling-project/docling-eval).
  - **ICDAR end-to-end text spotting**: IoU ≥ 0.5 *and* a correct transcription, with do-not-care regions [35](https://weinman.cs.grinnell.edu/pubs/weinman24counting.pdf).
- **Recommended:** per truth **word**, look up the predicted link(s) covering its markdown span. Each word gets one of four outcomes:
  - **correct**: the ParseBench IoA rule passes against the truth box.
  - **misplaced**: linked, but the rule fails.
  - **unlinked**: no link.
  - **ambiguous**: linked to more than one disjoint box.
- Report the misplaced rate as the headline location KPI. Also report median IoU, but don't gate on it.
- **Measured reason not to gate on IoU** (probe below): DOM rects use the font's content area (16 px = 12 pt for 14 px text), while the PDF text layer uses the em box (10.5 pt). A *perfect* answer therefore scores char IoU 0.784. Either convert both sides to one convention or use IoA / centre-in-box.

### (d) Coverage ledger (coordinator's proposal): prior art, names, unit

**Does anything already do it?** Every half exists under a different name. The combination does not:

| Coordinator's KPI | Standard name(s) | Where it exists | Needs ground truth? |
|---|---|---|---|
| Source words silently lost → 0 | **Deletions** (WER/CER S/D/I) [34](https://github.com/jitsi/jiwer); **Percent Tokens Found** = Σmin(f_pred, f_gt)/Σf_gt (bag of tokens, order-free) [23](https://github.com/Unstructured-IO/unstructured-eval-metrics); **omission** rules (word/sentence/digit) in ParseBench [22](https://arxiv.org/html/2604.08538v3); "segment lost" in DOCR-Inspector's 28 error types [41](https://arxiv.org/abs/2512.10619) | Yes, all against a reference | Yes, as published. **Ours runs against the source inventory instead** (see below) |
| Deliberately dropped, with reason | **Do-not-care / ignore regions** (ICDAR robust reading; docling-eval `ignore_zone_filter_type`) [35](https://weinman.cs.grinnell.edu/pubs/weinman24counting.pdf) [24](https://github.com/docling-project/docling-eval); olmOCR **absence** tests for headers/footers/page numbers [3](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py); boilerplate removal scored by token P/R/F1 (Bevendorff, SIGIR 2023) [33](https://webis.de/downloads/publications/slides/bevendorff_2023b.pdf) | Yes, but the *reason* is never part of the score | — |
| Flagged as unrepresented (plot region) | Nothing standard. Docling's layout postprocessor guarantees no text cell is orphaned: unclaimed cells get their own TEXT cluster [25](https://huggingface.co/spaces/wangrongsheng/AutoPage/blob/75725c1e906a367077d6e4331fe1330b6b8d177e/docling/utils/layout_postprocessor.py). It is a design invariant, not a metric | Implicit only | No |
| Markdown words unlinked (invented) → 0 | **Insertions**; **Percent Tokens Added** = Σmax(0, f_pred − f_gt)/Σf_pred [23](https://github.com/Unstructured-IO/unstructured-eval-metrics); ALCE **citation recall/precision** (NLI-judged) [32](https://arxiv.org/abs/2305.14627); ParseBench **LAP** (local attribution precision); ParseBench `repetition_overshoot`, budgeted from the PDF text layer with no GT [22](https://github.com/run-llama/ParseBench) | Yes | Published ones need GT, except `repetition_overshoot` |

- **Suggested name:** a **bidirectional word ledger** (or "reconciliation"), with KPIs **lost**, **dropped (by reason)**, **flagged**, **unlinked**.
- Map them onto deletions/insertions in the docs so readers recognise them.

**The ground-truth-free claim holds only with three guards** (my analysis):
1. **The source inventory must not come from the pipeline under test.**
   - Born-digital PDFs: the text layer is independent of Pipeline B but *is* Pipeline A's input, so A scores ~100% coverage trivially.
   - Filter invisible text first: render mode 3, white-on-white, clipped text, text hidden under images.
   - Scans: the inventory must come from a *second, different* OCR engine, or the ledger is circular.
2. **"Linked" is not "correct".** A pipeline that links every word to a page-sized box scores 100%. Add two GT-free checks:
   - **Box-tightness cap:** link area ≤ k × word area.
   - **Link-text agreement:** the source text under the linked box, from the text layer or a re-OCR of the crop, must match the markdown word after normalisation.
   - These still **cannot catch a link to the wrong *instance* of the same string**, e.g. "24" appears in ten cells of a multi-model table. That is exactly the high-value datasheet failure, and only round-trip or labelled GT catches it.
3. **Drop reasons can be gamed** (drop everything as "boilerplate"). Report the drop rate per reason. In the round trip, intended drops are tagged in the source (`<header>`, `<footer>`, nav), so wrong drops become measurable.

**Unit (word vs characters vs cells):**
- **Word as the ledger unit.** Word = maximal non-space run after NFKC + olmOCR normalisation.
  - It matches what text layers and OCR engines emit as boxes.
  - It is what a reviewer reads.
  - ParseBench and docling-eval OCR both default to it.
- **Characters only *within* a matched word** (CER of the pair), never as ledger rows. Ligatures, NFKC and hyphenation make a char ledger noisy: I measured one ﬄ glyph covering 3 markdown chars.
- **Plus a digit/numeric-token gate**, because "1.2" vs "12" is one word but the whole value.
- **Table cells as the unit inside tables.** Each source cell → a markdown cell at (row, col), with its header path. This matches SCORE's cell-content + cell-index accuracy [23](https://github.com/Unstructured-IO/unstructured-eval-metrics) and GriTS.

**Representation ladder** (share represented vs only flagged, by kind):
- Prior art slices scores by document attribute: OmniDocBench attributes, CC-OCR V2's "ten document factors" [27](https://arxiv.org/abs/2605.03903), ParseBench's five dimensions.
- Nobody reports "represented vs flagged". Use our kinds: prose · simple table · merged-header table · multi-model (column-per-model) table · footnote marker · plot (torque–speed curve) · diagram.
- For plots, truth = the series used to draw them.
  - Metric when represented: ParseBench ChartDataPointMatch (1% default tolerance) [22](https://arxiv.org/html/2604.08538v3), or range-normalised MAE with unmatched points as FN/FP [40](https://arxiv.org/abs/2503.12326).
  - In v0, flagged-only is the expected outcome.

---

## Ground-truth-box chain

**Options compared (least code first):**

| Chain | Granularity of source map | Code | Verdict |
|---|---|---|---|
| **Pandoc `commonmark_x+sourcepos` → HTML → headless Chrome `Range.getClientRects()` per char** (extension since pandoc 2.11.3 [36](https://hackage.haskell.org/package/pandoc-2.11.3.1/src/changelog.md)) | **Per token, line:col, both sides** (measured: every word *and* every punctuation run gets `<span data-pos="f@L:C-L:C">`, e.g. `1`,`.`,`2` separately; columns count code points: `·` = 1 col) | ~40 lines of JS + a page template | **Use this** |
| remark/mdast (`position.offset` on every node) → rehype → custom `data-sourcepos` plugin | Per text node (offsets), sub-node needs splitting | Needs a small rehype plugin + Node | Fine if the converter is JS; pandoc is already installed |
| markdown-it `token.map` | **Line ranges, block level only** | — | Too coarse |
| LaTeX + SyncTeX | **Line only**: TeX engines never write the column field (always −1/0) [38](https://lists.gnu.org/r/auctex/2015-02/msg00019.html) | Heavy | Reject |
| Typst `here().position()` / `locate(<label>)` | Per labelled element: page + x,y [39](https://typst.app/docs/reference/introspection/location/) | Would need every word wrapped in a function | Reject for v0 |
| DocBank-style colour coding + PDF char colours | Token | Recompile per document | Not needed when you own the renderer |

**The probe: what I actually ran.** Existing pandoc 3.1.3 and google-chrome, no installs. Files are in `/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/research/probe/`, scorer at `/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/research/compare_boxes.py`.
- **Input:** an 11-line datasheet-ish markdown (heading, bold value with units, 3×3 spec table, a ligature sentence).
- **Steps:**
  1. pandoc sourcepos → HTML.
  2. Fixed 816 px (8.5 in) page template with `@page{size:8.5in 11in;margin:0}`.
  3. An injected script records each char's rect into a hidden `<script type=application/json>`.
  4. `--dump-dom` for the boxes, `--print-to-pdf` for the PDF, `--screenshot --force-device-scale-factor=2` for the PNG.
- **Ligatures on (the Chrome default):**
  - The PDF text layer holds 158 chars against 166 DOM chars.
  - pdfplumber shows single glyphs `ﬀ ﬀ ﬂ ﬁ ﬀ ﬀ ﬄ`, so sequential alignment breaks at the first one.
  - `extract_words()` re-expands them, which hides the problem at word level.
- **Ligatures off** (`font-variant-ligatures:none; font-feature-settings:"liga" 0,"clig" 0`):
  - 166 = 166 chars, 0 mismatches.
  - Horizontal error (DOM px × 0.75 vs PDF pt): median 0.003 pt, **max 0.014 pt**.
  - Vertical: a systematic 2.1 pt offset because the box conventions differ (DOM 16 px = 12 pt content box vs PDF 10.5 pt em box). Char IoU is therefore 0.784 everywhere for a perfect answer.
- **PNG:** 1632×2112, which is exactly 2× CSS px.
- **Conclusion:** the chain is exact horizontally and is ~40 lines. Pick one vertical convention. Recommendation: take the **PDF text-layer char box** (PyMuPDF `rawdict` or pdfplumber `chars`) as the stored truth, joined by sequence to the DOM's markdown offsets, so PDF/PNG truth and PDF-parser output share a convention.

**Conditions the chain needs** (from the probe, plus Puppeteer/Chrome docs on screen-vs-print differences [37](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-printToPDF)):
- No `@media print` rules.
- Body width = page width.
- Fonts embedded and loaded before measuring.
- One fixed-size `<section class=page>` per page with `break-after: page`, so page index = section index and nothing depends on Chrome's pagination. **Multi-page is untested; I only probed one page.**
- Make the PNG by **rasterising the PDF** (`pdftoppm -r 150`, box_px = pt × 150/72), not by a second screenshot, so PDF and PNG share one geometry.
- The image-only arm is the same PNGs wrapped back into a PDF (img2pdf), with the text layer removed.
- Degradations:
  - v0 uses **photometric only** (Augraphy ink/paper phases, JPEG 70–95, blur, noise). These leave the boxes valid.
  - Geometric ones (rotation, warp, folds) need the box transform → v1.
- Rule of thumb from PubTabNet: **verify the truth by re-extraction.** For every truth char, the PDF text under its box must equal it; fail the case otherwise [12](https://arxiv.org/abs/1911.10683).

---

## Falsification — W5 → **SHARPENED**

The claim: *"Round-trip synthetic testing is valid and has prior art (DocBank, SynthDoG, PubLayNet, olmOCR-Bench…), but needs a small real labelled datasheet set too."*

1. **The prior-art list is half wrong. That matters, because it changes what to copy.**
   - DocBank, PubLayNet and PubTabNet are *alignment* corpora (existing source ↔ existing PDF). Nougat had to discard ~53% of pages to get clean pairs [17](https://arxiv.org/abs/2308.13418).
   - SynthDoG has no structure ground truth and no tables [16](https://arxiv.org/abs/2111.15664).
   - olmOCR-Bench is *real PDFs* with hand-checked tests. Its sibling olmOCR-synthmix is the round trip [1](https://arxiv.org/html/2510.19817v1).
   - The literal markdown → PDF precedent is **READoc-GitHub** [9](https://arxiv.org/html/2409.05137v3). Box-carrying synthetic precedents are DocBank (tokens), WordScape (blocks) and Infinity-Parser2 (blocks).
   - **None carries a markdown-offset → box table. That part is new, though small: probe above.**
2. **"Valid" holds only for measuring consistency against your own forward model. It does not show accuracy on real datasheets.** Evidence that clean renders overestimate:
   - READoc: the markdown-rendered GitHub subset beats arXiv and Zenodo by 7–35 points for most systems. The authors attribute this to GitHub's "uniform single-column layout" and "basic elements" [9](https://arxiv.org/html/2409.05137v3).
   - DocLayNet: PubLayNet → itself 93 mAP, → DocLayNet 30 mAP. Splitting by page instead of by document inflates mAP by ~10% [11](https://arxiv.org/abs/2206.01062).
   - olmOCR 2: base 99.7 vs old scans 47.7 [1](https://arxiv.org/html/2510.19817v1).
   - WordScape's source-derived table labels were mislabelled enough that Docling dropped them [19](https://arxiv.org/abs/2312.10188). Labels derived from the source are not automatically correct.
   - The robotics reading: this is sim-to-real. FK/IK consistency doesn't calibrate against the real arm. The standard fix is domain randomisation plus a real validation set.
3. **New, measured failure mode: the clean arm is biased toward Pipeline A by construction.**
   - Chrome's PDF text layer matches the DOM to 0.014 pt with every char present.
   - A parse-first pipeline that reads that layer gets near-perfect text *and* boxes. Real vendor datasheets have missing ToUnicode maps, outlined text, Type3 fonts and scanned pages.
   - Without the image-only and degraded arms, the harness would report "A ≫ B", which is W2, by construction. ParseBench's real-page result points the same way (parsers 66–81% vs VLMs < 10% on block grounding) [22](https://arxiv.org/html/2604.08538v3), but it must be *re-measured*, not assumed.
   - The HTML arm is trivially perfect for a DOM-reading converter (Defuddle). It tests only the DOM → markdown step, so don't count it as evidence for PDF/image.
4. **Markdown is too weak as the source for datasheets.**
   - It has no merged or multi-row headers, rotated tables, footnote anchors or plots.
   - The source must be markdown + raw HTML tables + SVG plots with known series. The "inverse" must be scored with structure metrics and unit tests, because many outputs are equally valid. This is the rationale behind both olmOCR's unit tests and SCORE [2](https://github.com/allenai/olmocr/tree/main/olmocr/bench) [23](https://mta-sts.unstructured.io/blog/introducing-score-bench-an-open-benchmark-for-document-parsing).
   - If an LLM writes the synthetic cases (olmOCR style), LLM-shaped content may favour the LLM pipeline. Hand-write or template the v0 cases.
5. **"A small real labelled set" needs a size, a split and a cheaper tier.**
   - **R0, real layouts with free location truth:** born-digital vendor datasheets → their (visible-filtered) text layer is the word-box truth → rasterise + degrade → run every pipeline on the image-only version. This gives coverage, ledger and location scores on real layouts with **zero labelling**. It is PubTabNet's verify-by-re-extraction idea, turned on ourselves.
   - **R1, human-verified values/structure:** ~25 datasheets from ≥ 10 vendors, ~15 labelled values each → ~375 value instances. This is my sizing, not a sourced number:
     - At 90% accuracy that gives roughly ±3 pp (95% CI).
     - With zero errors, the 95% upper bound on error is ~3/n ≈ 0.8% ("rule of three").
     - Use ParseBench's two-pass method (LLM proposes, human verifies) [22](https://arxiv.org/html/2604.08538v3).
     - **Split by vendor, never by page** (the DocLayNet lesson).
   - The coverage-ledger invariants run on *every* real document, but they can't see wrong-instance binding (guard 2). That is what R1 exists for.

**What the harness must include so it doesn't lie:** (i) image-only and photometric-degraded arms of every synthetic case; (ii) R0 real-layout arm; (iii) R1 vendor-split labelled set; (iv) results reported **per arm, never pooled**; (v) a frozen ruler — truth and tests generated before any pipeline runs, read-only to pipeline code, plus a mutation test (shift truth boxes by one line → location KPI must go red; delete a row → TEDS and ledger must go red); (vi) the box-convention fix, so a perfect answer scores perfect.

---

## Harness v0 design (smallest first version, stock parts)

**Cases (10–15, hand-written, templated):** `cases/<id>/source.md`, CommonMark + GFM + raw HTML for merged headers. Coverage:
- prose spec list
- simple spec table
- multi-model table (one column per model)
- merged two-row header
- units in headers
- footnote markers (`*1`)
- two-column page
- `<header>`/`<footer>` intended drops
- one torque–speed plot, drawn by matplotlib → SVG from a stored series

**Forward render** (`render.py`, Playwright-Python + Chromium as olmOCR uses; the `google-chrome` CLI suffices for a spike):
1. `pandoc -f commonmark_x+sourcepos -t html` → page template (fixed page sections, ligatures off, embedded fonts).
2. In-page script: per char `Range.getClientRects()` → `[md_line:col → md_offset, char, page, box_css]`.
3. `page.pdf()` → `clean.pdf`. Join to PyMuPDF/pdfplumber chars by sequence → `truth.jsonl` `{md_start, md_end, char, word_id, page, x0,y0,x1,y1 (pt)}`. **Self-check** that every char matches and x error < 0.05 pt; fail the case otherwise.
4. `pdftoppm -r 150` → PNG; `img2pdf` → `imageonly.pdf`. Augraphy (seeded, ink + paper phases only) + JPEG 70–95 → `degraded.pdf`.
5. Mine `tests.jsonl` in olmOCR-Bench schema from the source AST: present / absent / order / table / footnote / numeric-value-with-header-path.

**Inverse:** every pipeline (A, B, plus Docling and marker as stock comparison points) must emit `out.md` + `links.jsonl` `{md_start, md_end, page, x0,y0,x1,y1}`. Pipelines that cannot link still get text and table scores.

**Score** (`score.py`, ruler frozen and read-only):
- **Text:** NED (rapidfuzz) after `normalize_text` + NFKC; jiwer S/D/I.
- **Tests:** olmOCR `tests.py` pass rate.
- **Tables:** TEDS / TEDS-S (PubTabNet `metric.py`), GriTS-Con/Top, TableRecordMatch + transpose.
- **Digits:** numeric-token exact match with header path.
- **Location:** per word correct / misplaced / unlinked / ambiguous via the ParseBench IoA rule (0.50 / 0.20). Median IoU reported, not gated.
- **Ledger:** lost / dropped-by-reason / flagged / unlinked, with link-text agreement and a box-tightness cap. The source inventory is the truth on synthetic cases, and the text layer or a second OCR on R0.

**Report** (one self-contained HTML in the repo's `reports/`):
- KPI grid: pipeline × arm (clean / image-only / degraded / R0 / R1), never pooled.
- A representation-ladder bar per content kind.
- Per case: page image with truth boxes (green) vs predicted links (red), and lost/unlinked words highlighted.
- Word-level markdown diff (difflib); per-table TEDS with the two tables side by side; list of failed tests.
- Ruler self-check and mutation-test status at the top.

---

## Copy exactly

- **olmOCR** `olmocr/bench/tests.py` (Apache-2.0):
  - test classes and JSONL fields (`pdf, page, id, type, text, before, after, cell, up, down, left, right, top_heading, left_heading, max_diffs, first_n, last_n`)
  - `normalize_text`
  - fuzzy thresholds: `partial_ratio ≥ 1 − max_diffs/len`; tables `fuzz.ratio`, floored at 0.5 [3](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py)
- **olmOCR** `olmocr/synth/mine_html_templates.py` render path (Playwright Chromium HTML → PDF → PNG) and `augmentations.apply_jpeg_compression` (quality 70–95) [4](https://github.com/allenai/olmocr/tree/main/olmocr/synth) [5](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/synth/augmentations.py).
- **Pandoc** `-f commonmark_x+sourcepos -t html`: per-token `data-pos` (measured on pandoc 3.1.3).
- **CSS:** `font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0;` (measured: without it the char alignment breaks).
- **ParseBench** rules [22](https://arxiv.org/html/2604.08538v3):
  - localisation IoA(GT,Pred) ≥ 0.50 ∧ IoA(Pred,GT) ≥ 0.20
  - attribution token F1 ≥ 0.80
  - TableRecordMatch / GTRM
  - `bag_of_digit_percent`
  - ChartDataPointMatch (1% tolerance)
  - two-pass "VLM labels, human verifies" GT
- **SCORE** formulas: Tokens Found = Σmin(f_pred,f_gt)/Σf_gt; Tokens Added = Σmax(0,f_pred−f_gt)/Σf_pred [23](https://github.com/Unstructured-IO/unstructured-eval-metrics).
- **OmniDocBench** overall = ((1 − text NED)·100 + TEDS + CDM)/3, and `quick_match` block matching, if comparing against its leaderboard [7](https://github.com/opendatalab/OmniDocBench) [8](https://arxiv.org/abs/2412.07626).
- **TEDS:** PubTabNet `src/metric.py` (APTED). **GriTS:** `microsoft/table-transformer` `src/grits.py`.
- **jiwer** `process_words` for S/D/I counts [34](https://github.com/jitsi/jiwer).
- **Augraphy** pipeline (ink → paper → post phases) for degradation [30](https://arxiv.org/abs/2208.14558). DocCreator's models are the fallback for bleed-through and 3D paper [31](https://doi.org/10.3390/jimaging3040062); it is standalone desktop software, not a pip library, so it is v1 at most.
- **DocLayNet:** split evaluation sets by document/vendor, never by page [11](https://arxiv.org/abs/2206.01062).
- **PubTabNet:** verify every GT box by re-extracting its text from the PDF [12](https://arxiv.org/abs/1911.10683).

---

## Open gaps

- **No public word/char-level markdown → source grounding benchmark exists.** Our location metric is built from ParseBench's block rule applied per word. That is a judgement, not a standard.
- **Multi-page pagination is untested.** The probe was one page; fixed page sections should make it deterministic.
- **Geometric degradations** (rotation, warp, camera) need box transforms. This is deferred to v1.
- **Plots/diagrams:** markdown has no agreed representation. v0 only checks "flagged". Representing a curve as a data table + image is a spec decision for Zach.
- **Infinity-Synth / Infinity-Parser2's box-annotated synthesis engine** might be a more complete copyable generator. I could not locate or verify the repo.
- **ParseBench's grounding input format** (`layout.jsonl`) and dataset licence are not on the README. Check them before adopting its scorer wholesale.
- **R1 size (~25 docs / ~375 values)** is my statistical estimate, not a sourced norm. Revisit after the first 5 documents show the error rate.
- **Choice of vertical box convention** (PDF em box vs line box): I recommend the PDF text layer as truth, but OCR engines emit line-height-ish boxes. The IoA rule tolerates the difference; raw IoU won't.

---

# Source Index

All accessed 2026-10-29.

1. olmOCR 2: Unit Test Rewards for Document OCR (arXiv 2510.19817) — https://arxiv.org/html/2510.19817v1
2. allenai/olmocr `olmocr/bench` README — https://github.com/allenai/olmocr/tree/main/olmocr/bench
3. allenai/olmocr `olmocr/bench/tests.py` — https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/bench/tests.py
4. allenai/olmocr `olmocr/synth/` — https://github.com/allenai/olmocr/tree/main/olmocr/synth
5. allenai/olmocr `olmocr/synth/augmentations.py` — https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/synth/augmentations.py
6. LlamaIndex, olmOCR-Bench review: insights and pitfalls — https://www.llamaindex.ai/blog/olmocr-bench-review-insights-and-pitfalls-on-an-ocr-benchmark
7. opendatalab/OmniDocBench (v1.7) — https://github.com/opendatalab/OmniDocBench
8. OmniDocBench paper (arXiv 2412.07626, CVPR 2025) — https://arxiv.org/abs/2412.07626
9. READoc (arXiv 2409.05137v3, ACL Findings 2025) — https://arxiv.org/html/2409.05137v3
10. DocBank (arXiv 2006.01038, COLING 2020) — https://arxiv.org/abs/2006.01038
11. DocLayNet (arXiv 2206.01062, KDD 2022), Table 5 + split ablation — https://arxiv.org/abs/2206.01062
12. PubTabNet / TEDS (arXiv 1911.10683); code ibm-aur-nlp/PubTabNet — https://arxiv.org/abs/1911.10683
13. PubTables-1M (arXiv 2110.00061) — https://arxiv.org/abs/2110.00061
14. GriTS (arXiv 2203.12555); code microsoft/table-transformer — https://arxiv.org/abs/2203.12555
15. TableFormer (arXiv 2203.01017) — https://arxiv.org/abs/2203.01017
16. Donut / SynthDoG (arXiv 2111.15664) — https://arxiv.org/abs/2111.15664
17. Nougat (arXiv 2308.13418) — https://arxiv.org/abs/2308.13418
18. GOT-OCR 2.0 (arXiv 2409.01704) — https://arxiv.org/abs/2409.01704
19. WordScape (arXiv 2312.10188, NeurIPS 2023 D&B) — https://arxiv.org/abs/2312.10188
20. DocLayout-YOLO / DocSynth-300K (arXiv 2410.12628) — https://arxiv.org/abs/2410.12628
21. Infinity-Parser2 Technical Report (arXiv 2607.07836); Infinity-Parser (arXiv 2506.03197) — https://arxiv.org/abs/2607.07836
22. ParseBench (arXiv 2604.08538v3); code run-llama/ParseBench — https://arxiv.org/html/2604.08538v3 · https://github.com/run-llama/ParseBench
23. Unstructured, SCORE-Bench blog (2025-12-02); Unstructured-IO/unstructured-eval-metrics — https://mta-sts.unstructured.io/blog/introducing-score-bench-an-open-benchmark-for-document-parsing · https://github.com/Unstructured-IO/unstructured-eval-metrics
24. docling-project/docling-eval (README; `evaluators/bbox_text_evaluator.py`, `ocr_evaluator.py`) — https://github.com/docling-project/docling-eval
25. Docling `utils/layout_postprocessor.py` orphan clusters (HF mirror of docling source) — https://huggingface.co/spaces/wangrongsheng/AutoPage/blob/75725c1e906a367077d6e4331fe1330b6b8d177e/docling/utils/layout_postprocessor.py
26. OCRBench v2 (arXiv 2501.00321) — https://arxiv.org/abs/2501.00321
27. CC-OCR V2 (arXiv 2605.03903) — https://arxiv.org/abs/2605.03903
28. GutenOCR (arXiv 2601.14490) — https://arxiv.org/abs/2601.14490
29. PreciseDoc: An LMM for Precisely Grounding Elements in Documents (arXiv 2606.24118) — https://arxiv.org/abs/2606.24118
30. Augraphy (arXiv 2208.14558, ICDAR 2023); code sparkfish/augraphy — https://arxiv.org/abs/2208.14558
31. DocCreator (J. Imaging 2017, 3(4):62) — https://doi.org/10.3390/jimaging3040062
32. ALCE: Enabling LLMs to Generate Text with Citations (arXiv 2305.14627) — https://arxiv.org/abs/2305.14627
33. Bevendorff et al., An Empirical Comparison of Web Content Extraction Algorithms (SIGIR 2023), slides — https://webis.de/downloads/publications/slides/bevendorff_2023b.pdf
34. jitsi/jiwer (WER/CER with S/D/I) — https://github.com/jitsi/jiwer
35. Weinman, Gómez Grabowska & Karatzas, Counting the Corner Cases (ICDAR 2024, RRC ignore regions) — https://weinman.cs.grinnell.edu/pubs/weinman24counting.pdf
36. Pandoc changelog 2.11.3 (sourcepos extension, `data-pos`) — https://hackage.haskell.org/package/pandoc-2.11.3.1/src/changelog.md
37. Chrome DevTools Protocol `Page.printToPDF` (generateTaggedPDF etc.) — https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-printToPDF
38. AUCTeX list: SyncTeX column is always −1 — https://lists.gnu.org/r/auctex/2015-02/msg00019.html
39. Typst `location.position()` — https://typst.app/docs/reference/introspection/location/
40. Leveraging MLLMs for automated data extraction from plots (arXiv 2503.12326) — https://arxiv.org/abs/2503.12326
41. DOCR-Inspector (arXiv 2512.10619) — https://arxiv.org/abs/2512.10619

Local primary evidence: probe inputs and outputs in `/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/research/probe/` (`t.md`, `page.html`, `page2.html`, `dom2.html`, `page2.pdf`, `page2.png`). Scorer at `/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/research/compare_boxes.py`.
