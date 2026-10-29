# Researcher 2: Stage 2 and the seam. Grounded extraction, provenance formats, review UIs, datasheet prior art

Accessed 2026-10-29 (every web source below). Desk research only: docs, specs, source code and papers. The only local actions were reading robotparts files, running pandoc 3.1.3 and markdown-it-py 3.0.0 as already installed, and downloading two text files (DocLang `spec.md`, react-pdf-highlighter `types.ts`) into the scratchpad.

Terms I coin here:
- **span index**: a table that maps every element and table cell of *our* markdown to its code-point range in that markdown string and to its source location (page + box, or a DOM range).
- **cell addressing**: citing a table value by (table, row, column) or by a cell ID, as opposed to citing it by a quoted string.
- **binder**: code that turns a cited value into (row label, column header, unit, which part it belongs to). This is robotparts' `bind.py` job.

---

## Verdict

- **W3 SHARPENED.** "The LLM returns verbatim text and code aligns it" is the right default for prose and for rows where the label sits right before the number. It is what LangExtract, Instructor, LiteParse, LlamaExtract (`matching_text`) and Extend's default mode ("rules and string matching") all do [2](https://github.com/google/langextract/blob/main/langextract/resolver.py), [10](https://python.useinstructor.com/examples/exact_citations/), [24](https://developers.llamaindex.ai/liteparse/guides/visual-citations/), [16](https://developers.llamaindex.ai/llamaparse/extract/guides/extensions/index.md), [14](https://www.extend.ai/resources/product-spotlight-citations-reasoning). The catch is that a quote grounds a *string*, not a *table relation*. Every serious table path addresses cells instead:
  - Azure Content Understanding: `rowIndex`, `columnIndex`, `kind: columnHeader` [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements)
  - LandingAI: `table_cell` blocks with `row` and `col`; its Gen1 extract cited cell IDs [20](https://docs.landing.ai/dpt3/parse-response.md), [19](https://docs.landing.ai/ade/ade-extract-response.md)
  - Anthropic: custom-content blocks [8](https://platform.claude.com/docs/en/build-with-claude/citations)
  - Fonduer: `same_row`, `same_col`, `get_head_ngrams` [53](https://fonduer.readthedocs.io/en/latest/user/data_model_utils.html)

  The ceiling is already visible in our own code. The robotparts prompt tells the model to leave out any value "whose label sits only in a table's header row", and `bind.py` refuses column alignment on purpose [64](file:///home/bam/robotparts/tools/ingestor/llm.py). As a result, multi-model comparison columns are never extracted.
- **Answer to "cite IDs or quote text?" Use both.** Make the model cite the ID of a pre-segmented block or cell and copy the raw value. Code then checks the value is inside that cell and works out the row label and column header from the table structure.
  - For IDs: they form a closed set, so the model cannot point at nothing. Anthropic says its citations are "guaranteed to contain valid pointers" [8](https://platform.claude.com/docs/en/build-with-claude/citations), and Endex reported source hallucinations falling "from 10% to 0%" [9](https://claude.com/blog/introducing-citations-api).
  - Against IDs alone: a valid ID can still point at the wrong evidence. One study found link validity above 94% but factual accuracy of only 39–77% [28](https://arxiv.org/html/2605.06635v1).
  - Models are also much worse at choosing the column than the row. ViTaB-A measured 30% column vs 72% row accuracy on markdown, for 4B models [25](https://arxiv.org/html/2602.15769v1).
  - So code, not the model, should name the column.
- **W4 REVERSED for the internal map, HELD at the edges.** None of the document-extraction tools I checked use W3C Web Annotation. The standard shape in this space is `markdown` plus, for each element, a code-point span into that markdown plus a page and box:
  - Azure CU: `span{offset,length}` with `source: "D(page,x1,y1,…)"` [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements)
  - LandingAI: `grounding{page, range, box}` with word-level `atomic_grounding` [20](https://docs.landing.ai/dpt3/parse-response.md)

  Web Annotation is used in the Hypothesis, IIIF and Annotorious world [33](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/pdf.ts), [37](https://iiif.io/api/presentation/3.0/). DocLang is the new industry standard (LF AI & Data, spec v0.8), but its boxes are "only at the semantic element level". It has no character spans and no fetch provenance [47](https://github.com/doclang-project/doclang/blob/main/spec.md).
- **Fetch provenance: adopt as-is.**
  - HTML pages: WARC 1.1 (ISO 28500) written with warcio's `capture_http` [39](https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/), [40](https://github.com/webrecorder/warcio).
  - PDFs and images: name the fields of robotparts' existing `{url, sha256, retrieved_on, media_type}` record with PROV-O vocabulary [38](https://www.w3.org/TR/prov-o/).
  - Links to a source location: RFC 8118 `#page=N` for PDFs and Text Fragments `#:~:text=` for HTML [30](https://www.rfc-editor.org/rfc/rfc8118.html), [31](https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments).
- **Review UI: no local open-source tool links a value to its box. The building blocks exist.**
  - Docling's `--to html_split_page --show-layout` puts the page image with boxes beside the HTML [50](https://github.com/docling-project/docling/blob/main/docling/cli/main.py).
  - react-pdf-highlighter-extended (MIT) stores `ScaledPosition{boundingRect, rects[], pageNumber}` [51](https://github.com/agentcooper/react-pdf-highlighter).
  - pandoc `+sourcepos` maps rendered table cells back to markdown line:col (verified locally).
  - LangExtract's viewer shows text only, with no page image [4](https://github.com/google/langextract/blob/main/langextract/visualization.py).
  - The interaction to copy is the hosted playgrounds' "click field ↔ highlight source" (Reducto Studio) [13](https://docs.reducto.ai/configs/extract/citations).
- **Datasheet prior art.**
  - Fonduer (SIGMOD 2018, transistor datasheets) is the closest academic analogue, and its answer to tables is row/column structure plus visual alignment [53](https://arxiv.org/abs/1703.05028).
  - No datasheet vendor I checked (Octopart/Nexar, SiliconExpert, Flux, JITX) publicly documents per-value page+box provenance.
  - Curves: VLMs now beat DePlot on synthetic charts [61](https://arxiv.org/abs/2605.06021), but the best model scores 52.3% on 1,000 real curves [62](https://arxiv.org/abs/2605.27195). Treat values read off a plot as a separate kind of value: calibrated and checked by a person.

---

## Comparison tables

### T1. Grounded extraction: how each tool links a value to its source

| Tool (version) | What the model emits | How the location is resolved | Location returned | Tables | Usable locally / via `claude -p`? |
|---|---|---|---|---|---|
| **robotparts ingestor v0.3.0** | `{key, value, page, quote, raw, unit, note}` | `check.py` finds the whitespace-squashed quote on page text re-derived from the cached sha256 bytes. `bind.py` binds label to number | page + quote string | Flattened layout text. The prompt orders the model to omit values whose label is only in a header row. `bind.py` drops "a row of bare values" (one column per model) [64](file:///home/bam/robotparts/tools/ingestor/bind.py) | yes (it is the CLI) |
| **Google LangExtract 1.7.1** (Apache-2.0) [1](https://github.com/google/langextract), [7](https://pypi.org/project/langextract/) | `extraction_text` (verbatim), class, attributes | `WordAligner` runs in three steps [2](https://github.com/google/langextract/blob/main/langextract/resolver.py). (1) A monotonic exact token match by dynamic programming → `MATCH_EXACT`. (2) difflib `SequenceMatcher` blocks; a partial match → `MATCH_LESSER` (`accept_match_lesser=True`). (3) LCS fuzzy matching, with threshold `0.75` and density `1/3` → `MATCH_FUZZY`. An extraction that cannot be found gets `char_interval=None`. Repeated mentions go to successive occurrences in order | `char_interval` (char offsets in the input text) plus `token_interval` | No table logic. Issue #308 (long tables split from their header across chunks) is open. PR #535 (docs only) has not been merged [5](https://github.com/google/langextract/issues/308), [6](https://github.com/google/langextract/pull/535) | yes, with Gemini, OpenAI, Ollama or a custom provider plugin |
| **Instructor** `exact_citations` example (MIT) | `Fact{fact, substring_quote: List[str]}` | `re.finditer(re.escape(quote), context)`, exact match only. A quote that is not found is silently dropped, then facts with no quotes are dropped [10](https://python.useinstructor.com/examples/exact_citations/) | spans in the context | none | yes |
| **Anthropic Citations API** | The model cites internally; the API returns `cited_text` plus indices | The document is chunked first: sentences for text and PDF, or your own blocks for "custom content" documents. `cited_text` does not count toward output tokens [8](https://platform.claude.com/docs/en/build-with-claude/citations) | `char_location{start_char_index,end_char_index}` (0-indexed), `page_location{start_page_number,end_page_number}` (1-indexed, end exclusive), `content_block_location{start_block_index,end_block_index}` | Only by making each cell a custom-content block. "Image citations are not yet possible". A scanned PDF with no text "is not citable". **A request with both citations and structured outputs returns a 400 error** | API key only. Not available through Zach's logged-in CLI (inference: `claude -p` takes no `document` blocks) |
| **OpenAI file_search** | annotation `file_citation{index, file_id, filename}` | file-level only | no position inside the file [11](https://developers.openai.com/api/docs/guides/tools-file-search) | n/a | API |
| **Gemini File Search** (Interactions API) | `file_citation{file_name, source, page_number?, media_id}` | the page "may" be included | page at best [12](https://ai.google.dev/gemini-api/docs/file-search) | n/a | API |
| **Reducto Extract** | `citations[{type, content, bbox{left,top,width,height,page,original_page}, confidence, granular_confidence, parentBlock}]` | A localisation step after the answer ("could not reliably localize"). "Citations disable chunking" [13](https://docs.reducto.ai/configs/extract/citations) | sub-block box, normalised 0–1, plus the enclosing Parse block | A table value is cited as a text line inside the `Table` block. Spreadsheets use `left`=column, `top`=row, `page`=sheet | hosted |
| **Extend** | `citations` with a polygon for each field | By default "rules and string matching". Optionally, "the model outputs internal reasoning traces", which Extend resolves to coordinates. Both add latency [14](https://www.extend.ai/resources/product-spotlight-citations-reasoning) | polygon. `citationMode` line, word or block (changelog 2026-05-08, via search summary [15](https://docs.extend.ai/changelog/2026/5/8)) | not documented | hosted |
| **LlamaExtract** | `cite_sources: true` → `field_metadata.<leaf>.citation[{page, matching_text, bounding_boxes{x,y,w,h}, page_dimensions}]` [16](https://developers.llamaindex.ai/llamaparse/extract/guides/extensions/index.md) | matching text (implied by the field name) | page + boxes | leaf level | hosted |
| **LandingAI ADE** Gen1 → Gen2 (DPT-3) | **Gen1:** `extraction_metadata.<field> = {value, references}`. References are chunk UUIDs and **table-cell IDs `{page}-{base62}`** (e.g. `0-u`) [19](https://docs.landing.ai/ade/ade-extract-response.md). **Gen2:** `{value, ranges:[{start,end}]}`, Unicode code points into the parse markdown. `ranges: null` means "synthesized rather than copied" [18](https://docs.landing.ai/dpt3/extract-response.md) | `POST /v2/ground` joins a field's ranges to the blocks whose `grounding.range` overlaps them. It returns `{block_id, type, parent_id, grounding{page,range,box}}` plus the overlapping `atomic_grounding` entries [17](https://docs.landing.ai/api-reference/ground/ade-ground.md). How the ranges themselves are produced is not disclosed | block box, plus word/line `atomic_grounding` | `table_cell` blocks with `row`, `col`, `rowspan`, `colspan` and the cell's own range [20](https://docs.landing.ai/dpt3/parse-response.md) | hosted |
| **Azure AI Content Understanding** (API 2025-11-01) | fields with `estimateFieldSourceAndConfidence` | not disclosed | `span{offset,length}` into the top-level `markdown`, in code points (or UTF-16 / UTF-8 via `stringEncoding`), plus `source: "D(page,x1,y1,…,x4,y4)"` or `"D(page,l,t,w,h)"` in inches (PDF) or pixels (image) [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements) | `cells[{kind: columnHeader/rowHeader/stubHead/description/content, rowIndex, columnIndex, rowSpan, columnSpan, content, source, span, elements}]` | hosted |
| **Google Document AI** Custom Extractor | `Entity.text_anchor.text_segments` into `Document.text`, plus `page_anchor` and `provenance` [22](https://docs.cloud.google.com/python/docs/reference/documentai/latest/google.cloud.documentai_v1.types.Document) | not disclosed | text segments plus page polygons | via the layout model | hosted |
| **Sensible** | SenseML layout methods (`label`, `row`, `box` anchors) mixed with an LLM `queryGroup`. Some fields return `source` text [23](https://docs.sensible.so/docs/getting-started) | deterministic anchors | not shown in the getting-started output | `row` method | hosted |
| **LiteParse** (LlamaIndex) | no LLM. `search_items(page.text_items, phrase)` | phrase search over text items | `x,y,width,height` in PDF points from the top-left [24](https://developers.llamaindex.ai/liteparse/guides/visual-citations/) | `extract_blocks` gives cell boxes | local |

**Pattern:** tools that ground quoted strings (LangExtract, Instructor, LiteParse, robotparts) either have no table answer or refuse table values. Tools that ground tables (Azure CU, LandingAI, Reducto for spreadsheets, Fonduer) carry cell coordinates. The two tools that publish both the model output and the resolution step tell the story. Anthropic uses closed-set chunk indices. LandingAI moved from cell IDs (Gen1) to code-point ranges into markdown joined to cells (Gen2), and either way the end result is a cell with row/col and a box.

### T2. Provenance standards: adopt for (a) the markdown-span → source map, and (b) fetch provenance?

| Standard | What it actually encodes | Who uses it | (a) span → source map | (b) fetch provenance |
|---|---|---|---|---|
| **W3C Web Annotation Data Model** (Rec 2017-02-23) [29](https://www.w3.org/TR/annotation-model/) | `TextQuoteSelector{exact,prefix,suffix}` and `TextPositionSelector{start,end}`, both in **Unicode code points** of normalised text (tags stripped). `RangeSelector{startSelector,endSelector}`. `FragmentSelector{value, conformsTo}`, e.g. PDF `page=10&viewrect=50,50,640,480` (RFC 3778) or media `xywh=…`. `SvgSelector`. `refinedBy`. `TimeState{sourceDate, cached}`. `HttpRequestState` | Hypothesis, IIIF, Annotorious/Recogito, Apache Annotator, `anchorpoint` (Python). **No document-AI vendor** in T1 | **No as the internal format.** Borrow the semantics (code points, end exclusive, quote+position pair) and keep an export mapping | `TimeState.sourceDate` + `cached` fit, but are thin. Prefer WARC/PROV |
| **RFC 8118** (2017-03, obsoletes RFC 3778) [30](https://www.rfc-editor.org/rfc/rfc8118.html) | `page=`, `nameddest=`, `structelem=`, `comment=`, `zoom=`, `view=`, `viewrect=l,t,w,h`, `highlight=l,r,t,b`, `search=`. Units are 1/72 in from the top-left | Acrobat. pdf.js documents only `page`, `zoom`, `nameddest`, `pagemode` [32](https://github.com/mozilla/pdf.js/wiki/Viewer-options) | `#page=N` for "open source" links only. Draw boxes yourself | — |
| **Text Fragments** (WICG) [31](https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments) | `#:~:text=[prefix-,]start[,end][,-suffix]`. Case-insensitive, first match, each piece inside one block element | browsers | use for "open the live web page at this quote" links (the browser-native form of TextQuote) | — |
| **hOCR 1.2** (2020) [35](https://kba.github.io/hocr-spec/1.2/) | `ocr_page{bbox, image, imagemd5, ppageno}`, `ocr_line`, `ocrx_word{bbox x0 y0 x1 y1 (px), x_wconf}`. `x_bboxes` gives a box per code point | Tesseract | **Yes, as-is**, for the raw OCR word layer (Pipeline A input). `imagemd5` ties words to the exact page image | — |
| **ALTO 4.4** (LoC) [36](https://www.loc.gov/standards/alto/) | `String CONTENT HPOS VPOS WIDTH HEIGHT WC`, `Glyph` (v4), mandatory `MeasurementUnit` | libraries, newspapers; Tesseract/ABBYY output | equivalent to hOCR. Pick one, not both | — |
| **IIIF Presentation 3.0** [37](https://iiif.io/api/presentation/3.0/) | Canvases painted by W3C annotations. OCR and transcriptions are `supplementing` annotations | GLAM (galleries, libraries, archives, museums) | overkill | — |
| **DocLang v0.8** (LF AI & Data, launched 2026-06-09, Apache-2.0) [46](https://doclang.ai/), [47](https://github.com/doclang-project/doclang/blob/main/spec.md), [48](https://www.abbyy.com/company/news/finereader-engine-doclang-ai-native-document-standard/) | XML. Four `<location value=…/>` per semantic element on a `default_resolution` grid. "Coordinates are provided only at the semantic element level". OTSL tables (`<ched/>`, `<fcel/>`, `<nl/>`) | Docling, ABBYY FineReader Engine, Label Studio Enterprise export [49](https://labelstud.io/blog/docling-labelstudio) | **Watch it, don't adopt yet.** Element-level only, with no char spans or hashes. Could become the interchange format for the converter output | no |
| **Vendor de-facto shape** (Azure CU, LandingAI, Docling `prov`) | markdown string, plus per element `{span/range in code points}`, plus `{page, box/polygon}`, plus table cells with row/col | Azure, LandingAI, Docling, Reducto | **Copy this** (see Copy exactly) | — |
| **W3C PROV-O** (Rec 2013-04-30) [38](https://www.w3.org/TR/prov-o/) | `prov:Entity`, `prov:Activity`, `prov:Agent`, `prov:wasGeneratedBy`, `prov:used`, `prov:wasDerivedFrom`, `prov:generatedAtTime`, `prov:startedAtTime`, `prov:atLocation`, `prov:wasAttributedTo` | research data, RO-Crate | the "derived from source sha256 by converter vX" edge | **Use its terms as field names** (no RDF needed) |
| **WARC 1.1** (ISO 28500) + **warcio** (Apache-2.0) [39](https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/), [40](https://github.com/webrecorder/warcio) | `WARC-Target-URI`, `WARC-Date` (the instant capture began), `WARC-Payload-Digest` / `WARC-Block-Digest` as `algorithm:value` ("No particular algorithm is recommended", so `sha256:` is legal), request/response/metadata records, `WARC-Concurrent-To` | every web archive (Internet Archive, Webrecorder) | — | **Yes, as-is, for HTML fetches.** Use `with capture_http('x.warc.gz'): requests.get(url)` |

### T3. Source maps as a concept: is there a "source map for document conversion" format?

| Prior art | Granularity | Notes |
|---|---|---|
| **ECMA-426** source map format (1st ed. Dec 2024) [42](https://ecma-international.org/publications-and-standards/standards/ecma-426/) | generated (line, col) ↔ original (line, col), stored as Base64 VLQ `mappings` | The idea to copy: one compact table from output positions to source positions. It has no notion of a 2-D page box |
| **pandoc `+sourcepos`** (commonmark readers) [43](https://pandoc.org/MANUAL.html) | **Inline tokens, including table cells.** Verified locally with pandoc 3.1.3: `<th><span data-pos="1:3-1:8">Rated</span>…<span data-pos="1:9-1:15">torque</span></th><th><span data-pos="1:18-1:19">9</span></th>` | Best stock way to map a click on rendered markdown back to markdown line:col in the review page |
| **cmark `--sourcepos`** [44](https://github.com/commonmark/cmark) | `data-sourcepos` attribute. The man page says only "Include source position attribute". Block-level in practice (not verified) | — |
| **markdown-it-py 3.0.0** `Token.map` | **Line range only.** Verified locally: a table cell's inline token has `map=[0,1]`, with no column | not enough for cells |
| **SyncTeX** (J. Laurens, TUGboat 29(3), 2008) [45](https://tug.org/TUGboat/tb29-3/tb93laurens.pdf) | PDF position ↔ TeX source line, via a `.synctex.gz` side file | The closest widely used "conversion source map", but TeX-only and line-level. I could not fetch the PDF (403); this row is from memory |
| **Vendor inline maps** (Azure `span`+`source`, LandingAI `range`+`box`+`atomic_grounding`, Docling `prov.charspan`) | element and word level | Each vendor ships its own inline map. There is **no cross-tool standard** |

**Conclusion:** no shared "document conversion source map" format exists. The vendor inline shape is the de facto one, and ECMA-426 shows that a compact output→source table is enough when the output is frozen.

### T4. Review and verification UIs: what to copy

| Tool | What the reviewer sees | Copy? |
|---|---|---|
| LangExtract `lx.visualize()` [4](https://github.com/google/langextract/blob/main/langextract/visualization.py) | Self-contained HTML. Coloured `<span class="lx-highlight">` over the **input text**, a legend, and step-through animation. No page image | the legend and step-through, for the markdown pane |
| Docling `docling --to html_split_page --show-layout` (docling 2.145.0) [50](https://github.com/docling-project/docling/blob/main/docling/cli/main.py) | `HTMLOutputStyle.SPLIT_PAGE` + `LayoutVisualizer`: the page image with item boxes beside the HTML | **Yes**, as the left/right layout |
| Reducto Studio (`studio_link`), Extend, LlamaCloud, LandingAI ADE visualise [13](https://docs.reducto.ai/configs/extract/citations) | Click a field and its source is highlighted, and the reverse | **Yes**, the interaction. Hosted only |
| react-pdf-highlighter 8.0.0-rc.0 / **react-pdf-highlighter-extended 8.1.0** (both MIT) [51](https://github.com/agentcooper/react-pdf-highlighter), [52](https://www.npmjs.com/package/react-pdf-highlighter-extended) | pdf.js page with highlights. `ScaledPosition{boundingRect: Scaled{x1,y1,x2,y2,width,height,pageNumber}, rects: Scaled[], pageNumber, usePdfCoordinates}`. Supports scroll-to-highlight | **Yes**, for the PDF pane, if the page is live pdf.js rather than a PNG |
| Hypothesis (pdf.js) [33](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/pdf.ts), [34](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/match-quote.ts) | Quotes anchored in the pdf.js text layer. Position is a hint; on a miss it fuzzy-searches the quote (`approx-string-match`, `maxErrors = min(256, len/2)`, weights quote 50 / prefix 20 / suffix 20 / position 2). Unrendered pages get placeholders | Its fallback ladder is the design to copy for *HTML* sources that change |
| LiteParse [24](https://developers.llamaindex.ai/liteparse/guides/visual-citations/) | `sharp` composites a yellow rectangle onto a page PNG at `scale = dpi/72` | a static-report fallback (PNG per value) |
| Label Studio + Docling ML backend / DocLang UI [49](https://labelstud.io/blog/docling-labelstudio) | Draw element boxes and reading order; .dclx export is Enterprise-only | for building W5's labelled set, not for review |

### T5. Datasheet-specific prior art

| Item | What it does | Provenance of values | Lesson |
|---|---|---|---|
| **Fonduer** (Wu, Hsiao, … Ré; SIGMOD 2018) [53](https://arxiv.org/abs/1703.05028) | Builds knowledge bases from "richly formatted" documents, including electronics datasheets. Average +41 F1 over expert-curated knowledge bases | Mentions keep cell row/col and boxes. Helpers include `same_row`, `same_col`, `get_row_ngrams`, `get_col_ngrams`, `get_head_ngrams` ("the row's leftmost cell and/or the column's topmost cell"), `get_aligned_ngrams`, `is_horz_aligned`, `same_page` [53](https://fonduer.readthedocs.io/en/latest/user/data_model_utils.html) | **The binder is a structure lookup**: the row header and column header of a cell. This is what `bind.py` cannot do on flattened text |
| D2S-FLOW (2025) [54](https://arxiv.org/abs/2502.16540) | LLM workflow that extracts datasheet parameters for SPICE models. EM 0.86, F1 0.92 | none stated in the abstract | accuracy without grounding |
| "Fidelity Is Not Enough" (Ye & Lin, EMNLP 2026 Industry) [55](https://arxiv.org/abs/2608.28439) | Agentic datasheet extraction, with tool-call logging | the trace of tool calls | "One model passed our fidelity check without ever opening the datasheet". "A structured-output constraint had silently disabled tool use". **Log what the model was actually given**, and require the cited ID's content to contain the value |
| JITX `jitx-component-modeler` skill [56](https://www.claudepluginhub.com/plugins/jitx-inc-jitx-skills) | Builds component code from datasheets. Pages via PyMuPDF; a person checks the first family | "datasheet is the authority"; blocked when no source is found | a human checkpoint per family |
| Flux Copilot [57](https://www.flux.ai/p/blog/ditch-datasheets-with-this-flux-copilot-upgrade) | Reads a part's Datasheet URL. Inline citations since July 2023 | link level | — |
| Octopart / Nexar [58](https://support.nexar.com/support/solutions/articles/101000541169-octopart-and-nexar-api-unified-data-for-a-better-consistent-experience) | `specs{attribute{name}, displayValue}`, `bestDatasheet` | **No per-value source location documented** | the gap robotparts fills |
| SiliconExpert [59](https://www.siliconexpert.com/proof/trusted-intelligence) | "automation, AI, and human domain expertise" for validation | not public | — |
| Circuit Mind, SnapMagic | — | nothing public found | — |
| **Curves.** WebPlotDigitizer (frontend AGPL-3.0; "AI Assist" is closed) [60](https://github.com/automeris-io/WebPlotDigitizer). PlotPick [61](https://arxiv.org/abs/2605.06021). EpiCurveBench [62](https://arxiv.org/abs/2605.27195). ChartZero [63](https://arxiv.org/abs/2605.05820). Azure CU figure analysis exports line charts as Chart.js JSON [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements) | PlotPick: 9 VLMs score 79.1–96.0% vs DePlot's 74.3% (numeric F1 at 5% tolerance) on synthetic ChartX. EpiCurveBench: the best of 6 methods reaches 52.3% on 1,000 real curves. ChartZero: specialist line extractors (LineFormer, LineEX) degrade outside their training data | The provenance of a curve value is (page, figure box, pixel x,y, axis calibration). It is not a text span | A torque–speed point is a **different value kind**. Always overlay the extracted series on the figure for review |

---

## Falsification

### W3: "LLM returns verbatim spans, code aligns them to char offsets; no need for the LLM to emit coordinates" → **SHARPENED**

What holds:
- No production system I found asks the LLM for pixel coordinates in the extraction step. Reducto, Extend, LandingAI and Azure all localise *after* or *outside* the model's answer [13](https://docs.reducto.ai/configs/extract/citations), [14](https://www.extend.ai/resources/product-spotlight-citations-reasoning), [17](https://docs.landing.ai/api-reference/ground/ade-ground.md).
- Text → char offsets → boxes is the standard chain, so "no coordinates from the LLM" holds.

What breaks:
- **Tables.** A quote grounds a string. A datasheet value is a *relation*: (row label, column header = which model, unit) → number. In a comparison table the column header ("AK80-9") sits rows above the number, so no contiguous quote in reading order holds both.
- **Duplicates.** The same number ("48") can appear in several columns. LangExtract's aligner then simply takes occurrences in output order [2](https://github.com/google/langextract/blob/main/langextract/resolver.py).
- **Robotparts already hit this wall.** `llm.py` says: "A value whose label sits only in a table's header row, above a row of values, cannot be checked: leave it out". `bind.py` says: "Not done on purpose: aligning a table's header row with a data row by column index… Such values are dropped instead" [64](file:///home/bam/robotparts/tools/ingestor/bind.py).
- **The recall cost is invisible.** Across the 45 cached runs (`~/.cache/robotparts-ingest/runs/*/verdicts.json`: 477 kept, 25 dropped), **0 drops cite the bare-row rule**, because the prompt removes those values before the checker ever sees them.
- **Models are worst at exactly that column choice.** In ViTaB-A, row accuracy beat column accuracy 71.6 vs 30.0 on markdown and 61.0 vs 44.2 on images (4B models). JSON tables were near random (<1%) [25](https://arxiv.org/html/2602.15769v1).

**(a) Do production systems make the model cite IDs of pre-segmented spans instead? Both patterns are in production. Neither alone is enough for tables.**

| Failure mode | Verbatim quote + align | Cite an ID of a pre-segmented span or cell | **ID + raw value, both checked** |
|---|---|---|---|
| Model invents a location | caught (quote not found) | impossible (closed set; Anthropic: "guaranteed to contain valid pointers" [8](https://platform.claude.com/docs/en/build-with-claude/citations)) | caught |
| Model normalises characters (µ/μ, −/-, ±, Ω, ligatures, thin spaces, decimal comma) | exact match fails, so you need fuzzy matching (LangExtract 0.75; Hypothesis allows edits up to len/2), and fuzzy matching can anchor a *number* to the wrong place | not affected | compare digits after NFKC normalisation inside one known cell |
| Same value in several places | ambiguous (LangExtract picks occurrences in order) | the ID disambiguates | the ID disambiguates |
| Label and number not contiguous (column header far above) | cannot quote both, so the value is dropped (robotparts) | code reads the header from the structure | the same, plus the value check |
| Valid pointer, wrong evidence | not applicable (content is checked) | **not caught**: link validity >94% vs accuracy 39–77% [28](https://arxiv.org/html/2605.06635v1) | caught: the value must be in the cell, and the cell's column header must name this part |
| Model answers from memory [55](https://arxiv.org/abs/2608.28439) | partly caught (the quote must exist) | not caught (an ID can be attached to a memorised value) | caught: the value must be printed in the cited cell |
| Value is a sub-span of a cell ('18V~36V' → min/max) | yes | cell only | yes (the value's offset inside the cell) |
| Output tokens and latency | the quote costs tokens | cheap (`cited_text` is free [8](https://platform.claude.com/docs/en/build-with-claude/citations)) | small |
| Works with `claude -p` + JSON output | yes | yes, as a prompt convention. The Citations *API* needs a key, and its 400 error with structured outputs rules it out anyway | yes |

Why IDs are more reliable at the pointer level:
- The model chooses from a set it was shown, instead of reproducing characters.
- Anthropic measured "recall accuracy by up to 15%" over do-it-yourself prompting, and Endex reported 10% → 0% source hallucinations [9](https://claude.com/blog/introducing-citations-api).
- LandingAI's Gen1 shipped exactly this for tables (cell IDs `0-u`) [19](https://docs.landing.ai/ade/ade-extract-response.md).

Why IDs alone are not enough:
- A pointer that is valid but wrong passes.
- The model is weakest at picking the column [25](https://arxiv.org/html/2602.15769v1).

Hence the sharpened W3:
> Stage 2 = **cite-then-verify over a span index**. The markdown is pre-segmented into blocks and table cells with stable IDs, and the model sees those IDs. The model returns `{key, value, raw, cell_or_block_id}`. Code checks four things: `raw` occurs in that block or cell; `value` is printed in `raw`; the row label comes from the cell's row-header cells (or first column); the column header comes from the column-header cells and, for multi-model tables, must carry this part's designation. A verbatim quote stays the fallback for prose. It goes through `check.py` (strict) or LangExtract's `WordAligner` (fuzzy).
>
> A cheaper variant that keeps the quote design: **linearise each cell into a self-describing line** before the model sees it (`[t2.r4.c3] AK80-9 · Rated torque (Nm) · 9`). Then a contiguous quote *can* hold the label, the header and the value, and the line's ID carries its own span-index entry.

**(b) Is W3C Web Annotation used by document-extraction tools?** **No.** None of the 13 tools in T1 emit it, and neither does Docling, DocLang, Label Studio (its export is its own JSON or DocLang) or react-pdf-highlighter (`ScaledPosition`). Its real users are annotation-on-the-web and GLAM tools: Hypothesis, IIIF, Annotorious, Apache Annotator, Pundit [33](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/pdf.ts), [37](https://iiif.io/api/presentation/3.0/). A search for Web Annotation exports in Label Studio, Docling or INCEpTION found none.

### W4: "Express provenance in an existing standard (W3C selectors; hOCR/ALTO) rather than a home-made format" → **REVERSED for the internal span map; HELD for the edges**

Why reversed:
- **Web Annotation solves a problem we don't have.** Its selectors exist to re-find a span after the target document changes; that is why Hypothesis keeps quote + position + fuzzy fallback [33](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/pdf.ts). Our markdown is a frozen derived artifact keyed by (source sha256, converter version), so a code-point range is stable by construction, and the quote is only a checksum.
- **It doesn't express what we need.** The hard part is 2-D: page boxes, word boxes and table cells. Web Annotation offers only `FragmentSelector` (`viewrect=` in RFC 8118, `xywh=` media fragments) and `SvgSelector`, and no PDF viewer in this space honours them. pdf.js documents only `page`, `zoom`, `nameddest` and `pagemode` [32](https://github.com/mozilla/pdf.js/wiki/Viewer-options).
- **The ecosystem already converged elsewhere.** The document-AI ecosystem settled on "span into markdown + page + box + cell row/col" (Azure CU, LandingAI, Docling) [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements), [20](https://docs.landing.ai/dpt3/parse-response.md). Copying that shape gives drop-in comparison with the hosted tools, which W2/W5 need.
- **DocLang doesn't change this.** It is an industry standard, but element-level only (four `<location>`s on a resolution grid, "only at the semantic element level") [47](https://github.com/doclang-project/doclang/blob/main/spec.md). It is worth tracking as the converter's interchange format, not as the char-level map.
- **An export stays cheap if we ever need one.** Mapping to Web Annotation later is about 50 lines: range → `TextPositionSelector` (same code-point, end-exclusive semantics) plus `TextQuoteSelector`; box → `FragmentSelector` `page=N&viewrect=l,t,w,h` in points (RFC 3778 conformsTo).

Why held at the edges:
- hOCR (or ALTO) for the raw OCR word layer.
- WARC via warcio for HTML fetches.
- PROV-O names for the fetch record.
- RFC 8118 `#page=` and Text Fragments `#:~:text=` for "open the original here" links.

All of these are real, widely implemented standards with off-the-shelf readers and writers.

---

## Copy exactly

1. **The span-map *format*: LandingAI ADE DPT-3 `grounding`, with Azure CU `span`/`source` as the second witness.** [20](https://docs.landing.ai/dpt3/parse-response.md), [21](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements)
   - Top-level `markdown`, with `metadata.range_units = "unicode_codepoints"`.
   - Blocks: `{id: "<type>-<n>", type, grounding: {page, range: {start, end}, box: {xmin, ymin, xmax, ymax} (0–1)}, atomic_grounding: [{page, range, box}] (words), children}`.
   - Table cells: `table_cell` blocks with `row, col, rowspan, colspan`. Add Azure's `kind` (`columnHeader`/`rowHeader`/`stubHead`/`content`); LandingAI lacks it, and the binder needs it.
   - The extraction leaf: `{value, ranges | null, cell_or_block_id}`. `ranges: null` marks a "synthesised" value.
   - The join: LandingAI's `/v2/ground` semantics (field ranges ∩ block ranges → boxes), implemented locally.
   - For HTML sources, `box` becomes a DOM range (XPath/CSS + offsets) plus a Text Fragment link.
2. **The extraction *pattern*: Anthropic's custom-content documents and LandingAI Gen1's cell-ID references, done with `claude -p`.**
   - Show the model block and cell IDs. Ask for `{key, value, raw, id}`.
   - Verify `raw` is inside the cell or block, `value` is inside `raw`, and the header is read from the structure.
   - Keep robotparts' `check.py` (stricter than LangExtract) for prose quotes.
   - Don't use the Citations API itself: it needs an API key, and it returns a 400 error when combined with structured outputs [8](https://platform.claude.com/docs/en/build-with-claude/citations).
3. **The stock parts at the edges:**
   - warcio `capture_http` writing WARC 1.1, with `WARC-Payload-Digest: sha256:…` as fetch provenance (PROV-O field names for PDFs).
   - hOCR from Tesseract if OCR is used.
   - For the review page: Docling's `html_split_page --show-layout` layout as the starting point; `react-pdf-highlighter-extended` 8.1.0 `ScaledPosition` for PDF highlights; pandoc `-f commonmark_x+sourcepos` so a click on a rendered markdown cell maps back to its markdown span.

**The one missing piece to build: the span index and its structural binder.**
- What exists: stock parsers give element-level provenance and table structure (Docling `prov`, table cells with header flags).
- What no open-source tool gives: the code-point range of each element or cell *inside the markdown string it exports*, for markdown we or a VLM write.
- What to build:
  - A serializer (or aligner, the W1/W2 seam) that records, for every element and cell it writes, `{id, range, row/col/kind, page, box}`.
  - A `bind.py` rewrite that reads row and column headers from that index instead of guessing from flattened layout text.
- Everything above it is stock.

---

## Open gaps

- **How Gen2 / Azure CU / Reducto produce field ranges is undisclosed.** It could be model-emitted IDs mapped to ranges, or a post-hoc string match. I found no vendor or academic head-to-head of ID citation vs quote citation on the *same* extraction task. Measure it on our own labelled datasheet set (W5): same pages, three arms (quote, ID, ID + value).
- **Frontier-model column accuracy on datasheet comparison tables is unmeasured.** ViTaB-A used 2–38B open models; the 38B model reached about 58% on markdown with chain-of-thought [25](https://arxiv.org/html/2602.15769v1).
- **The recall lost to the "leave column-headed values out" prompt rule is unknown**, because those values are never proposed. Count comparison-table values in the labelled set to size it.
- **WebPlotDigitizer's project/calibration JSON** (the pixel → data provenance) was not verified from a primary source this session. The README does not describe it.
- **DocLang is moving fast** (repo updated 2026-10-29, spec v0.8). Re-check for word or char spans and source metadata before deciding against it as the converter output.
- **Gemini File Search** says `page_number` "may" be included. **OpenAI** gives file-level citations only. Neither is a grounding option for us.
- Not verified first-hand: Extend's `citationMode` (from a search summary of its 2026-05-08 changelog), LlamaExtract's field names (search summary of its docs), cmark sourcepos granularity, and SyncTeX details (the PDF returned 403).
- **Inference, not tested:** that `claude -p` cannot pass Citations-enabled `document` blocks.

---

# Source Index

All accessed 2026-10-29.

1. https://github.com/google/langextract
2. https://github.com/google/langextract/blob/main/langextract/resolver.py
3. https://github.com/google/langextract/blob/main/langextract/core/data.py (AlignmentStatus: MATCH_EXACT, MATCH_GREATER, MATCH_LESSER, MATCH_FUZZY)
4. https://github.com/google/langextract/blob/main/langextract/visualization.py
5. https://github.com/google/langextract/issues/308
6. https://github.com/google/langextract/pull/535
7. https://pypi.org/project/langextract/ (1.7.1)
8. https://platform.claude.com/docs/en/build-with-claude/citations
9. https://claude.com/blog/introducing-citations-api (page dated 2025-06-23)
10. https://python.useinstructor.com/examples/exact_citations/
11. https://developers.openai.com/api/docs/guides/tools-file-search
12. https://ai.google.dev/gemini-api/docs/file-search
13. https://docs.reducto.ai/configs/extract/citations
14. https://www.extend.ai/resources/product-spotlight-citations-reasoning (2025-05-08)
15. https://docs.extend.ai/changelog/2026/5/8 (via search summary)
16. https://developers.llamaindex.ai/llamaparse/extract/guides/extensions/index.md (via search summary)
17. https://docs.landing.ai/api-reference/ground/ade-ground.md
18. https://docs.landing.ai/dpt3/extract-response.md
19. https://docs.landing.ai/ade/ade-extract-response.md
20. https://docs.landing.ai/dpt3/parse-response.md
21. https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements (updated 2026-10-19)
22. https://docs.cloud.google.com/python/docs/reference/documentai/latest/google.cloud.documentai_v1.types.Document (via search summary)
23. https://docs.sensible.so/docs/getting-started
24. https://developers.llamaindex.ai/liteparse/guides/visual-citations/
25. https://arxiv.org/html/2602.15769v1 (ViTaB-A, 2026-02-17)
26. https://aclanthology.org/2024.emnlp-demo.26 (MATSA)
27. https://arxiv.org/abs/2602.13059 (TraceBack / CITEBench, via search summary)
28. https://arxiv.org/html/2605.06635v1 (Cited but Not Verified, via search summary)
29. https://www.w3.org/TR/annotation-model/ (W3C Rec 2017-02-23)
30. https://www.rfc-editor.org/rfc/rfc8118.html (2017-03)
31. https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments ; https://wicg.github.io/scroll-to-text-fragment/
32. https://github.com/mozilla/pdf.js/wiki/Viewer-options
33. https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/pdf.ts
34. https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/match-quote.ts
35. https://kba.github.io/hocr-spec/1.2/
36. https://www.loc.gov/standards/alto/
37. https://iiif.io/api/presentation/3.0/
38. https://www.w3.org/TR/prov-o/ (W3C Rec 2013-04-30)
39. https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/
40. https://github.com/webrecorder/warcio
41. (unused)
42. https://ecma-international.org/publications-and-standards/standards/ecma-426/ (1st ed. Dec 2024)
43. https://pandoc.org/MANUAL.html (sourcepos; behaviour verified locally with pandoc 3.1.3)
44. https://github.com/commonmark/cmark (man page `cmark.1`)
45. https://tug.org/TUGboat/tb29-3/tb93laurens.pdf (403 this session; cited from memory)
46. https://doclang.ai/
47. https://github.com/doclang-project/doclang/blob/main/spec.md (Version 0.8)
48. https://www.abbyy.com/company/news/finereader-engine-doclang-ai-native-document-standard/ ; https://www.theregister.com/ai-and-ml/2026/06/16/a-modest-proposal-reformat-everything-to-make-documents-more-palatable-to-ai/5255938 (via search summary)
49. https://labelstud.io/blog/docling-labelstudio (2026-08-25)
50. https://github.com/docling-project/docling/blob/main/docling/cli/main.py (docling 2.145.0 on PyPI)
51. https://github.com/agentcooper/react-pdf-highlighter (src/types.ts; npm 8.0.0-rc.0, MIT)
52. https://www.npmjs.com/package/react-pdf-highlighter-extended (8.1.0, MIT)
53. https://arxiv.org/abs/1703.05028 ; https://fonduer.readthedocs.io/en/latest/user/data_model_utils.html
54. https://arxiv.org/abs/2502.16540
55. https://arxiv.org/abs/2608.28439
56. https://www.claudepluginhub.com/plugins/jitx-inc-jitx-skills ; https://docs.jitx.com/en/latest/jumpstart-kits/js1-stackup-components/part2-parametric-passives/JS1_Part2_Parametric-Passives_Runbook.html (via search summary)
57. https://www.flux.ai/p/blog/ditch-datasheets-with-this-flux-copilot-upgrade (via search summary)
58. https://support.nexar.com/support/solutions/articles/101000541169-octopart-and-nexar-api-unified-data-for-a-better-consistent-experience (via search summary)
59. https://www.siliconexpert.com/proof/trusted-intelligence (via search summary)
60. https://github.com/automeris-io/WebPlotDigitizer
61. https://arxiv.org/abs/2605.06021 (PlotPick v2, 2026-10-06)
62. https://arxiv.org/abs/2605.27195 (EpiCurveBench)
63. https://arxiv.org/abs/2605.05820 (ChartZero, via search summary)
64. Local: /home/bam/robotparts/tools/ingestor/llm.py (SYSTEM_PROMPT), /home/bam/robotparts/tools/ingestor/bind.py (module docstring), /home/bam/robotparts/tools/ingestor/check.py; /home/bam/.cache/robotparts-ingest/runs/*/verdicts.json (45 files, tallied 2026-10-29)
