# [1] Stage 1 converters: "anything → markdown" and what provenance each one emits

Researcher 1 of 3 · desk research only (source code on GitHub, official docs, papers) · all sources accessed 2026-10-29.

Terms used below:
- **Provenance / source map**: for a piece of output markdown, where it came from in the input: page + box for a PDF or image, or a character range in the fetched HTML.
- **Granularity**: the smallest unit that has its own location. From coarsest to finest: page, block (paragraph, table, figure), line, word, char.
- **Joinable**: the markdown string itself, or a field next to it, tells you which output characters belong to which located unit. Without that, you get boxes *and* markdown but no link between them.
- **Text layer**: the characters a born-digital PDF already contains, each with an exact box. It needs no OCR.
- **Aligner**: code that matches output text to source text character by character (edit-distance alignment) and carries the source locations across.
- **Spotting**: a model reads text *and* returns a box for each line or word.

## Verdict

- **W1 SHARPENED. No open-source converter gives a word- or char-level map of its own markdown.** The best open-source tools (Docling, Marker 2.1, MinerU 4, PaddleOCR-VL, dots, Chandra 2) stop at block level. Docling's `charspan` turns out to be `(0, len(text))` of each item's own text, or `(0, 0)` from VLM pipelines, so it adds nothing below block level [14](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py), [15](https://github.com/docling-project/docling/blob/main/docling/models/stages/reading_order/readingorder_model.py). The newest model-based releases have become *coarser*: Surya 2 swapped `text_lines` for `blocks` [43](https://github.com/datalab-to/surya), MinerU 4 removed boxes from inline spans [31](https://github.com/opendatalab/MinerU/blob/master/docs/next/middle-json/current-medium.md), and olmOCR stopped feeding the text layer to its model [35](https://github.com/allenai/olmocr/blob/main/olmocr/pipeline.py).
- **The exact target already exists, but only as hosted services.** Azure Document Intelligence and Content Understanding return one markdown string in which every word, line, paragraph and table cell has `span{offset,length}` into that string plus a polygon [49](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements), [48](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0). LlamaParse `granular_bboxes: ["word","line","cell"]` gives byte spans into each item's markdown, each with a box [50](https://developers.llamaindex.ai/llamaparse/parse/features/layout-and-bounding-boxes/), [51](https://developers.llamaindex.ai/llamaparse/parse/examples/parse_granular_bboxes/). **Copy their output format; build the locally run engine yourself.**
- **W2 HELD, with one caveat about which models.** On *location*, layout-aware pipelines beat models that read the page image alone. ParseBench block grounding: Azure 73.8, Textract 70.4, Reducto 68.7, Docling 66.1; image-only models Gemini 3 Flash 56.0, dots.ocr 1.5 55.8, Qwen3-VL 55.2; general assistant models Haiku 4.5 6.7, GPT-5 Mini 6.2 [68](https://arxiv.org/abs/2604.08538). On *reading structure*, vision-language models win: the top six on OmniDocBench v1.6 are all specialised models scoring 95–97, and every one with a published size has 1.3B parameters or fewer, against MinerU-Pipeline 86.5 and Marker 78.4 [66](https://github.com/opendatalab/OmniDocBench). The caveat: frontier models can now spot lines (OCRBench v2: Gemini 3 Pro 80.8, Qwen3.6-27B 74.3). **The models behind Zach's claude/codex CLIs cannot: Claude Opus 4.6 scores 7.4 and GPT-5.2 2.2** [70](https://99franklin.github.io/ocrbench_v2/). Never ask the CLIs for coordinates; ask them for text, then align it.
- **A hybrid already exists, but only at block or line level.** Docling's VLM pipeline `force_backend_text=True` uses boxes from the VLM and text from the PDF inside each box [20](https://github.com/docling-project/docling/blob/main/docling/pipeline/vlm_pipeline.py). Marker reads the text layer and sends only bad blocks to its VLM [24](https://github.com/datalab-to/marker). local-llm-pdf-ocr binds LLM lines to detected line boxes with Needleman-Wunsch, a sequence-alignment algorithm [71](https://github.com/ahnafnafee/local-llm-pdf-ocr/blob/main/src/pdf_ocr/core/aligner.py). **No open-source tool aligns model-written markdown to text-layer characters. That aligner is the one missing piece.**
- **Defuddle: HELD as the way to pick out main content from HTML, REVERSED as a source of provenance.** It clones the document, serialises `outerHTML` and converts with Turndown, so it keeps no mapping back to the page. Debug mode returns only the CSS path of the chosen content element and a log of what it removed [2](https://github.com/kepano/defuddle/blob/main/src/defuddle.ts), [1](https://github.com/kepano/defuddle). Its hidden-element and mobile-CSS checks only fully work in a real browser [5](https://github.com/kepano/defuddle/blob/main/src/removals/hidden.ts). Pair it with jsdom's `nodeLocation()`, which gives char offsets into the fetched HTML [12](https://github.com/jsdom/jsdom#getting-the-source-location-of-a-node-with-nodelocationnode), and with Web Clipper's own anchor format (xpath + offsets + prefix/suffix) [7](https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/highlighter.ts).
- **Local options on the 16 GB RTX 4080:**
  - PaddleOCR-VL-1.6: 0.96B, Apache-2.0, 96.3 on OmniDocBench v1.6; block boxes plus a `Spotting:` line mode [37](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md), [40](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5).
  - MinerU2.5-Pro: 1.2B.
  - GLM-OCR: 1.3B, MIT.
  - dots.mocr: 3B.
  - Chandra 2: 5.3B, under an OpenRAIL licence with a revenue cap.

  Docling already ships parsers for most of their outputs (`docling/utils/{paddleocr_vl,dots,mineru,chandra,deepseekocr}_utils.py`), so they all land in one document model.

## Comparison table

Granularity means the finest unit that has its own location. "Joinable" means the markdown can be tied to those locations without writing new code.

| Tool (version, date) | What it is | Finest provenance | Coordinates | Markdown joinable? | Tables | License | Local on 16 GB | Maturity |
|---|---|---|---|---|---|---|---|---|
| **Defuddle** 0.19.6 (2026-10-24) [1](https://github.com/kepano/defuddle) | HTML → main-content HTML/markdown | **none**; debug mode gives the CSS path of the content element and removals as step/selector/reason/text | n/a | no; DOM is cloned, then `outerHTML`, then Turndown | simple → pipe table; rowspan/colspan → raw HTML; layout tables flattened [3](https://github.com/kepano/defuddle/blob/main/src/markdown.ts) | MIT | yes (Node or browser) | 9.8k★, active |
| Mozilla Readability [9](https://github.com/mozilla/readability) | HTML → article HTML | none | n/a | no | HTML passthrough | Apache-2.0 | yes | 11.5k★ |
| Trafilatura 2.3.1 (2026-10-06) [10](https://github.com/adbar/trafilatura) | HTML → txt/md/XML/JSON | none (no source positions in the code) | n/a | no | md/XML | Apache-2.0 | yes | 7.0k★ |
| MarkItDown 0.1.9 (2026-10-21) [11](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_pdf_converter.py) | anything → md | none; uses pdfplumber word positions internally to build tables, then discards them | n/a | no | md pipe | MIT | yes | 193k★ |
| **Docling** 2.145.0 / docling-core 2.105.0 (2026-10-29) [14](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py) | PDF/image/HTML/Office → `DoclingDocument` → md/HTML/JSON | **block** (`prov[] = page_no, bbox, charspan`; `TableCell.bbox` per cell [23](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/items/table/table_data.py)). Char/word/line cells exist only in `ConversionResult.pages[i].parsed_page`, kept with `generate_parsed_pages=True` [18](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/page.py), [19](https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py) | PDF points, bottom-left origin | **partly**: `SerializationResult.spans` lists the contributing items but has **no offsets** (`prov_idx` is commented out) [16](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/serializer/base.py) | md / HTML, cells with boxes | MIT | yes | 69.8k★, releases weekly |
| Docling HTML backend `render_page` [22](https://github.com/docling-project/docling/blob/main/docling/backend/html_backend.py) | HTML → rendered pages | block: stamps `data-docling-id` on tags, renders in Playwright with JS off, records each tag's box | page pixels | via items | as Docling | MIT | yes | (in Docling) |
| **Marker** 2.1.0 (2026-10-29) [24](https://github.com/datalab-to/marker), [28](https://github.com/datalab-to/marker/releases) | PDF/image → md/JSON/HTML/chunks | **block** (JSON leaves are blocks with `polygon`, `bbox`, `id=/page/N/Type/i` [25](https://github.com/datalab-to/marker/blob/master/marker/renderers/json.py)); char boxes only via `OCRConverter --keep_chars` on digital pages [27](https://github.com/datalab-to/marker/blob/master/marker/renderers/ocr_json.py) | PDF points, top-left | block: HTML `add_block_ids` → `data-block-id` [26](https://github.com/datalab-to/marker/blob/master/marker/renderers/html.py); none in md | md pipe or `--html_tables_in_markdown` | Apache code; weights OpenRAIL-M (free under $5M revenue) | yes (Surya VLM via vLLM) | 40.7k★; v2 rewrite (2026-07) |
| **MinerU** 4.1.2 (2026-10-29) [29](https://github.com/opendatalab/MinerU), [30](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md) | PDF/image/Office/HTML → md / MiddleJson 2.0 | **block** (bbox normalised to [0,1]); **inline spans no longer carry boxes** in 4.x [31](https://github.com/opendatalab/MinerU/blob/master/docs/next/middle-json/current-medium.md). Locator `doc:/tier:/page:/block:[/char:N]` [33](https://github.com/opendatalab/MinerU/blob/master/mineru/doclib/locators.py) | normalised 0–1 | **block**: optional md markers `<!-- mineru:block {"ref":…} -->` + citation record (bbox, source_sha256) [32](https://github.com/opendatalab/MinerU/blob/master/docs/next/decisions/0012-doclib-block-locator.md) | HTML | "MinerU Open Source License" (Apache-2.0 + conditions) | yes (8 GB+ VRAM tiers) | 82.8k★ |
| olmOCR 2 (v0.4.27, 2026-03) [34](https://github.com/allenai/olmocr/blob/main/olmocr/prompts/prompts.py) | page image → md + YAML front matter | **none** (figures named `page_x_y_w_h.png`); anchoring now only used for the pdftotext fallback [35](https://github.com/allenai/olmocr/blob/main/olmocr/pipeline.py) | n/a | no | HTML | Apache-2.0 | 7B: FP8 only | 19.8k★, quiet since 2026-03 |
| dots.ocr / **dots.mocr** (renamed 2026-03-19) [36](https://github.com/studio-dots-ai/dots.ocr) | page image → JSON `{bbox, category, text}` per element + md | block | input-image pixels `[x1,y1,x2,y2]` | via JSON | HTML | MIT | yes (3B) | 9.2k★ |
| **PaddleOCR-VL-1.6** / PP-StructureV3 (PaddleOCR 3.7.0) [37](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md), [38](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md) | layout detector + 0.9B VLM per block | block (`parsing_res_list[].block_bbox`). **Line** in StructureV3 `overall_ocr_res.rec_polys`; **word** with `return_word_box` [39](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md); VL `Spotting:` prompt localises text lines [40](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) | image pixels | block via JSON | HTML | Apache-2.0 | yes | 91.9k★ |
| Chandra 2 (5.3B, 2026-03) [41](https://github.com/datalab-to/chandra) | page image → HTML/md/JSON with layout | block (boxes on a 0–1000 grid [42](https://github.com/docling-project/docling/blob/main/docling/utils/chandra_utils.py)) | normalised | via JSON | HTML | weights OpenRAIL-M (free under $2M revenue; no use competing with Datalab's API) | yes (about 11 GB bf16) | 12.6k★ |
| Surya 2.5 (0.7B, 2026-10-29) [43](https://github.com/datalab-to/surya) | OCR VLM + line detector | block (`blocks[].html`, polygon); line detector gives polygons **without text** | pixels | via JSON | HTML | Apache code; weights OpenRAIL-M | yes | 21.5k★ |
| GLM-OCR (1.3B) [44](https://github.com/zai-org/GLM-OCR) | PP-DocLayout-V3 regions → VLM | block | pixels | via JSON | HTML | MIT | yes | 7.6k★ |
| DeepSeek-OCR 2 (3B) [77](https://github.com/deepseek-ai/DeepSeek-OCR) | `<\|grounding\|>` mode emits `<\|ref\|>label<\|det\|>[[x1,y1,x2,y2]]` per element | block | 0–1000 | inline tags in md | HTML/md | MIT | yes | 24.1k★ |
| granite-docling-258M [78](https://huggingface.co/ibm-granite/granite-docling-258M) | VLM → DocTags with location tokens | block | grid | via DoclingDocument | yes | Apache-2.0 | yes | IBM |
| Nougat [45](https://github.com/facebookresearch/nougat) / GOT-OCR2 [46](https://github.com/Ucas-HaoranWei/GOT-OCR2.0) | image → md/LaTeX | none (GOT accepts a box as *input* only) | n/a | no | md/LaTeX | MIT / no licence file | yes | stale since 2025-02 |
| **Azure Document Intelligence v4 / Content Understanding** [47](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/markdown-elements?view=doc-intel-4.0.0), [49](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements) | hosted layout → markdown | **word** (also line, paragraph, cell, figure, formula) | polygon in inches (PDF) or pixels (image); CU: `D(page,x1,y1,…,x4,y4)` | **YES, natively**: `span{offset,length}` into the top-level markdown, in code points (UTF-16/UTF-8 optional) | HTML `<table>` with rowspan/colspan inside the md | commercial | no | GA |
| **LlamaParse** (agentic tiers) [50](https://developers.llamaindex.ai/llamaparse/parse/features/layout-and-bounding-boxes/) | hosted → items tree + md | **word / line / cell** (`granular_bboxes`); raw `word_bbox` JSONL | page points `{x,y,w,h,r}` | **YES, per item**: `span` = `[start,end)` **UTF-8 byte** range into the item's `md` | cells with spans | commercial | no | — |
| Mistral OCR 4 [52](https://docs.mistral.ai/capabilities/document_ai/basic_ocr) | hosted → per-page md | block (`include_blocks`), image boxes; word *confidences* but no word boxes | pixels | block | md or HTML (`table_format`) | commercial | no | — |
| Unstructured 0.27.35 [53](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/documents/elements.py) | partition → elements | block (`metadata.coordinates.points` + `system`) | PixelSpace / PointSpace | element ↔ text | `text_as_html` | Apache-2.0 | yes (hi_res) | 15.6k★ |
| Reducto [54](https://docs.reducto.ai/parse/response-format) | hosted | block (`bbox{left,top,width,height,page}`) | 0–1 | block | HTML/md | commercial | no | — |
| AWS Textract + Textractor [55](https://github.com/aws-samples/amazon-textract-textractor) | hosted OCR + layout; Textractor `to_markdown()` | **word** (`Geometry.BoundingBox`/`Polygon`) | 0–1 ratios | **near**: `get_text_and_words()` returns the md text plus the ordered word list, but no offsets | md/HTML | commercial (SDK Apache) | no | — |
| Google Document AI [56](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk) | hosted OCR / Layout Parser | OCR: **token (word)** with `textAnchor` start/end index into `document.text`. Layout Parser: block `bounding_box` (opt-in `return_bounding_boxes`) | normalised vertices | text yes, but **no markdown output** | blocks | commercial | no | — |
| pymupdf4llm 0.3.4 [57](https://github.com/pymupdf/pymupdf4llm/blob/main/src/helpers/pymupdf_rag.py) | PDF → md | **near-word**: `extract_words=True` puts a `words` list in markdown order in each page chunk (no offsets) | PDF points | near | md/HTML | AGPL-3.0 | yes | 2.3k★ |
| pdfplumber 0.11.10 `page.chars` [58](https://github.com/jsvine/pdfplumber) · PyMuPDF `get_text("rawdict")` [59](https://pymupdf.readthedocs.io/en/latest/app1.html) · docling-parse `char_cells` [18](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/page.py) | PDF text layer | **char** (x0, x1, top, bottom; PyMuPDF adds `origin`) | PDF points | n/a (no markdown) | n/a | MIT · AGPL · MIT | yes | mature |
| pdf.js `getTextContent()` [60](https://github.com/mozilla/pdf.js) | browser text layer | text **run** (`str, transform, width, height`); chars only by measuring DOM ranges | PDF space | n/a | n/a | Apache-2.0 | yes | 54k★ |
| Tesseract hOCR/TSV [61](https://github.com/tesseract-ocr/tesseract) · docTR [62](https://github.com/mindee/doctr) | OCR | **word** (Tesseract: char with `hocr_char_boxes`) | pixels · 0–1 | n/a | n/a | Apache-2.0 | yes | mature |
| Qwen3-VL (8B) [63](https://github.com/QwenLM/Qwen3-VL/tree/main/cookbooks) | VLM | block (`qwenvl html`: `data-bbox` per element); **line or word** spotting prompt → `bbox_2d` | relative 0–999/1000 | via HTML attributes | HTML / LaTeX | Apache-2.0 | 8.8B → needs FP8/AWQ | 20k★ |
| Gemini 3 [64](https://ai.google.dev/gemini-api/docs/image-understanding) | hosted VLM | whatever you prompt: `box_2d [ymin,xmin,ymax,xmax]` | 0–1000 | no | — | commercial | no | — |
| Florence-2-large [65](https://huggingface.co/microsoft/Florence-2-large) | 0.77B VLM | line (`<OCR_WITH_REGION>` quad boxes) | pixels | n/a | none | MIT | yes | 2024, quiet |
| Claude / GPT via `claude -p` / `codex exec -i` | general assistant model | whatever you prompt; **measured poor** (spotting 7.4 / 2.2) [70](https://99franklin.github.io/ocrbench_v2/) | pixels if asked | no | — | subscription | n/a | — |

Leaderboards, to choose a model that reads structure well:
- **OmniDocBench v1.6.** Overall = mean of (1 − text edit distance)×100, table tree-edit similarity (TEDS) and formula image-match score (CDM). Top scores: OXR-1.0 97.03 (not released); TeleOCR 1.2B 96.91; OvisOCR2 0.8B 96.47; PaddleOCR-VL-1.6 0.9B 96.34; MinerU2.5-Pro 1.2B 95.75; GLM-OCR 0.9B 95.22. Further down: Gemini 3 Pro 92.91; dots.ocr 90.77; MinerU-Pipeline 86.47; olmOCR 85.74; Mistral OCR 85.66; Marker 78.44 [66](https://github.com/opendatalab/OmniDocBench).
- **olmOCR-Bench**: Datalab API 86.7; Chandra 2 85.8; dots.ocr 1.5 83.9; olmOCR 2 82.4 [41](https://github.com/datalab-to/chandra). Marker 2.1 balanced scores 76.0 on its own run, docling 50.3, MinerU-pipeline 72.7 [24](https://github.com/datalab-to/marker).
- **Caveat**: neither benchmark scores *location*. ParseBench scores location, but only at block level and it is run by the LlamaParse team [68](https://arxiv.org/abs/2604.08538).

## Falsification

### W1: "Don't build a converter; a stock parser emits element-level provenance; we only add the char-level map." **SHARPENED (partly reversed on Docling).**

- **Broken: "Docling `prov` = page + bbox + charspan" implies char-level information.**
  - `ProvenanceItem` is `page_no, bbox, charspan` [14](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py).
  - Every producer in Docling writes `charspan=(0, len(text))` into the item's *own* text, or `(0, 0)` from the VLM, dots, PaddleOCR-VL, Chandra and DeepSeek adapters.
  - The only non-trivial value is when two fragments are merged across pages or columns: the second prov gets `(len(prev)+1, len(prev)+1+len(new))` [15](https://github.com/docling-project/docling/blob/main/docling/models/stages/reading_order/readingorder_model.py).
  - Docling's own Azure-format exporter says so outright: *"Word-level segmentation is not available in the DoclingDocument"* [17](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/serializer/azure.py).
  - The markdown serializer returns `SerializationResult(text, spans: list[Span(item)])`, with **no offsets** into `text` [16](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/serializer/base.py).
  - So we must also build the **markdown → item** map (serialise item by item and record offsets), not just the item → char map.
- **Held, and the data is there:**
  - Docling keeps the finer data in the conversion result, not the document. `PdfPipelineOptions(generate_parsed_pages=True)` keeps `conv_res.pages[i].parsed_page`, a `SegmentedPdfPage` with `char_cells`, `word_cells` and `textline_cells` from docling-parse (MIT) [18](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/page.py), [19](https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py).
  - Docling's new model-free `NativePdfPipeline(text_cell_unit=WORD|CHAR)` turns every word or char into an item with its own box [21](https://github.com/docling-project/docling/blob/main/docling/pipeline/native_pdf_pipeline.py). That is useful as an independent oracle, meaning a check that does not share code with the thing being checked.
- **Sharpened by (a), a tool that already maps its markdown at word level:**
  - **Azure DI / Content Understanding** puts all output in one markdown string. Words, lines, paragraphs, table cells, formulas and barcodes each carry `span{offset,length}` into that string (Unicode code points by default) plus a 4-point polygon or `source: "D(page,x1,y1,…,x4,y4)"` [49](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements). For Document Intelligence v4, the markdown lives in `content` and the docs note that selection-mark spans point at the ☒/☐ characters in that markdown [48](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0).
  - **LlamaParse** `granular_bboxes` gives per-item `grounding.lines[].words[]`, each with `span` (UTF-8 bytes into the item's `md`) and a box [51](https://developers.llamaindex.ai/llamaparse/parse/examples/parse_granular_bboxes/).
  - **Consequence:** the stock part to copy *exactly* is Azure's output contract, not a converter. Neither service gives char boxes; within a word, char positions are interpolated.
- **New counter-evidence on trend:** open source is moving *away* from fine provenance as converters switch to VLMs.
  - Surya 2: "`text_lines` → `blocks` (with `html`)" [43](https://github.com/datalab-to/surya).
  - Marker: pages that go through the VLM "return block-level HTML without character boxes" [24](https://github.com/datalab-to/marker).
  - MinerU 4: "bbox, score and font info are not part of InlineSpan" [31](https://github.com/opendatalab/MinerU/blob/master/docs/next/middle-json/current-medium.md). MinerU's own citation design caps public locators at block level, with no span or cell locators in phase 0 [32](https://github.com/opendatalab/MinerU/blob/master/docs/next/decisions/0012-doclib-block-locator.md).
  - Do not expect an upstream release to fill this gap.

### W2: "Parse-first beats model-only on location; VLMs win structure; best is a hybrid with a deterministic aligner." **HELD, sharpened on which models.**

- **Location, block level (ParseBench).** An element passes when overlap is ≥0.5 against ground truth and ≥0.2 the other way, the class is right, and the text matches at token F1 ≥0.8 [68](https://arxiv.org/abs/2604.08538).
  - Hosted pipelines: Azure 73.8, Textract 70.4, Reducto 68.7, Docling 66.1, Google 61.3.
  - Image-only models: Gemini 3 Flash 56.0, dots.ocr 1.5 55.8, Qwen3-VL 55.2.
  - General assistant models: Haiku 4.5 6.7, GPT-5 Mini 6.2. The paper: grounding is "difficult for proprietary single-pass VLMs".
  - LlamaParse Agentic leads at 80.6, but its own team wrote the benchmark.
- **Location of extracted values.** ExStrucTiny (J.P. Morgan AI Research, EACL 2026) asked models for `{text, page, bbox 0–1000}` per extracted value. Best mean box overlap (IoU): Gemini-2.5-Pro 14.4%, Qwen2.5-VL-72B 10.3%, Qwen2.5-VL-7B 3.1%. The authors found that correct text did not reliably come with a correct box [69](https://arxiv.org/abs/2602.12203). This is the strongest evidence against asking the model that extracts a value to also say where it is.
- **Caveat that sharpens W2 (looked for (b), found it partly).** By late 2026, frontier and specialised models have become good at line-level spotting. OCRBench v2 English spotting, private set, 2026.09 board: Gemini 3 Pro Preview 80.8, Qwen3.6-27B 74.3, Qwen3.5-9B 41.8. Claude Opus 4.6 scores 7.4, GPT-5.2 2.2, GPT-5 6.5 [70](https://99franklin.github.io/ocrbench_v2/). (Prompt format matters: Qwen2.5-VL-7B goes from 3.1 to 51.6 with its native `bbox_2d` prompt.) So model-only location is **not** hopeless in general, but it is with the models Zach can run from his CLIs today. Opus 5.x and GPT-6 are unmeasured; the round-trip test harness should measure them.
- **Structure.** Specialised VLMs dominate OmniDocBench v1.6, and the gap is largest on tables (TEDS 94.8 for PaddleOCR-VL-1.6 against 65.8 for Marker) [66](https://github.com/opendatalab/OmniDocBench). Held.
- **Hybrids that already exist (b)**, none at char level:
  - Docling VLM pipeline `force_backend_text=True`: "get text from backend using bounding boxes predicted by SmolDocling doctags" [20](https://github.com/docling-project/docling/blob/main/docling/pipeline/vlm_pipeline.py). Box from the VLM, text from the PDF, block level.
  - Marker fast/balanced: text from pdftext; blocks with garbled or empty text get "surgical" VLM repair; tables rebuilt from the text layer [24](https://github.com/datalab-to/marker). Block level.
  - local-llm-pdf-ocr `HybridAligner`: Surya line boxes plus LLM transcription, joined by a monotonic Needleman-Wunsch over (LLM lines, boxes) with a char-count-vs-box-area cost [71](https://github.com/ahnafnafee/local-llm-pdf-ocr/blob/main/src/pdf_ocr/core/aligner.py). Line level, for scans, 128★.
  - Azure and LlamaParse Agentic produce word-level spans, which implies they align internally, but the method is not documented.
  - **No open-source tool aligns model-written markdown to text-layer chars or OCR words at char level.** Stock alignment algorithms exist for building it: edlib (infix mode finds a short string inside a long one and returns the alignment path) [72](https://github.com/Martinsos/edlib), RapidFuzz `Levenshtein.opcodes` [73](https://github.com/rapidfuzz/RapidFuzz), and genalog's anchor-based OCR alignment (stale since 2024, useful as a reference) [74](https://github.com/microsoft/genalog).
- **Sharpening for datasheets.** Robot-part datasheets are mostly born-digital PDFs. There, the text layer already gives exact char boxes, and aligning model-written markdown to it is close to exact string matching. Fuzzy alignment is only needed for scans and images. For those, use word boxes from PaddleOCR `return_word_box`, docTR or Tesseract, or a spotting VLM: PaddleOCR-VL `Spotting:` or Qwen3.x word-level `bbox_2d`.

### Defuddle: "the key reference; mixes straightforward and AI." **HELD for content selection, REVERSED for provenance.**

- **Pipeline** [2](https://github.com/kepano/defuddle/blob/main/src/defuddle.ts):
  1. Site-specific extractor, if one matches.
  2. Read the page's mobile media-query styles (≤600 px).
  3. `cloneNode`, flatten shadow DOM, apply mobile styles.
  4. `findMainContent`: score the `ENTRY_POINT_ELEMENTS` (`#post`, `.post-content`, `article`, `main`, `#content`, … `body`) plus a content score; fall back to table-based and schema.org text detection.
  5. Remove: metadata block, small images, hidden elements, exact selectors (~240, including `nav`, `[role=navigation]`, `.menu`, sticky headers) and partial patterns (~610, including `-menu`, `nav-`, `submenu`, `dropdown`) [4](https://github.com/kepano/defuddle/blob/main/src/constants.ts).
  6. `ContentScorer.scoreAndRemove`: link density, navigation words, comma counts; e.g. link density >0.5 is a strong non-content signal [6](https://github.com/kepano/defuddle/blob/main/src/removals/scoring.ts).
  7. Content patterns, standardisation (footnotes, code, math, callouts), sanitising, then `outerHTML`, then Turndown markdown.
- **Mapping kept: none.**
  - It works on a clone, so the DOM nodes in the output are not the original nodes.
  - `content` is a serialised string.
  - Debug mode adds only `contentSelector` (CSS path) and `removals[{step, selector, reason, text}]`. Outside debug mode it also strips `data-*` attributes, apart from an allow-list [1](https://github.com/kepano/defuddle), [4](https://github.com/kepano/defuddle/blob/main/src/constants.ts).
- **The "AI" half in Obsidian** is Web Clipper's Interpreter. It sends the cleaned content to an LLM and gets back `prompts_responses` markdown strings, with **no citations** [8](https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/interpreter.ts). Zach's project adds exactly the grounding Obsidian lacks.
- **What to copy for maker sites that put the whole catalogue in nav menus:**
  - Selector removal, link-density scoring and the removal log. The log doubles as provenance for what was *dropped*.
  - **Run it in a real (headless) browser.** In Node, `getComputedStyle` is skipped and external stylesheets are not loaded [5](https://github.com/kepano/defuddle/blob/main/src/removals/hidden.ts). A mega-menu hidden by CSS then survives unless a selector or the link-density score catches it.
  - Keep `contentSelector` per maker domain as the override.
- **Kepano already has a DOM anchor format.** Web Clipper highlights are stored as `{xpath, startOffset, endOffset, textQuote{prefix, suffix}}` per block element [7](https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/highlighter.ts). That is the HTML half of the provenance format, from the same author.

## Copy exactly

1. **Output contract: Azure Content Understanding's element shape** [49](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements).
   - One `markdown` string per document.
   - Every word, line, paragraph and table cell carries `span: {offset, length}` in **Unicode code points** into that string.
   - Each also carries `source: "D(page, x1,y1, x2,y2, x3,y3, x4,y4)"`, 1-based page; PDF units in inches.
   - For HTML sources, the source side becomes a char range into the sha256-pinned fetched bytes plus Web Clipper's `xpath/startOffset/endOffset/textQuote` [7](https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/highlighter.ts).
   - Researcher 2 owns how this maps onto W3C selectors.
2. **PDF and image engine: `docling` 2.145 (MIT).**
   - Run `DocumentConverter` with `PdfPipelineOptions(generate_parsed_pages=True)`.
   - Read `conv_res.document` (items with `prov[].page_no/bbox`, `TableCell.bbox`) together with `conv_res.pages[i].parsed_page.{char_cells, word_cells, textline_cells}` [19](https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py), [18](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/page.py).
   - For scans, or where layout is hard, run Docling's VLM pipeline with a local PaddleOCR-VL or granite-docling model, and `force_backend_text=True` when a text layer exists [20](https://github.com/docling-project/docling/blob/main/docling/pipeline/vlm_pipeline.py).
   - Independent char-box oracle: `pdfplumber` `page.chars` (MIT).
   - Higher-quality alternative for structure: `marker-pdf` 2.1 JSON with `add_block_ids` (`data-block-id="/page/N/Type/i"` joined to JSON `polygon`) [26](https://github.com/datalab-to/marker/blob/master/marker/renderers/html.py). Its weights are licensed OpenRAIL-M.
3. **HTML engine: `defuddle` 0.19.6 (MIT), run in headless Chromium, on a DOM whose nodes were stamped with ids first.**
   - Get source offsets from `new JSDOM(html, {includeNodeLocations: true})` + `dom.nodeLocation(node)` → `{startOffset, endOffset}` into the HTML string, including for text nodes [12](https://github.com/jsdom/jsdom#getting-the-source-location-of-a-node-with-nodelocationnode).
   - Keep the ids through Defuddle either with `debug: true`, which keeps `data-*` but changes div flattening, or with a one-line allow-list patch to `ALLOWED_ATTRIBUTES` [4](https://github.com/kepano/defuddle/blob/main/src/constants.ts).
   - Docling's HTML backend shows the same id-stamping trick in Python (`data-docling-id` + Playwright boxes) [22](https://github.com/docling-project/docling/blob/main/docling/backend/html_backend.py).
   - If the markdown step moves to unified, `hast-util-to-mdast` already copies source `position` onto every node [13](https://github.com/syntax-tree/hast-util-to-mdast/blob/main/lib/state.js).

**The one missing piece to build: a markdown → source aligner.**
- (a) A serializer that records, for every *content* character of the markdown (not markdown syntax such as `#` or `|`), which source item it came from. For Docling, serialise per item and track offsets; for HTML, track per node.
- (b) Within each item, align its text to that item's source chars or words with edlib infix or RapidFuzz opcodes [72](https://github.com/Martinsos/edlib), [73](https://github.com/rapidfuzz/RapidFuzz). This is exact on born-digital PDFs and fuzzy on OCR or model output.
- (c) Emit Azure-shaped spans, with a per-span alignment score so unaligned model text, such as hallucinations or normalised characters, is flagged rather than silently located.
- The round-trip test harness from the brief is how to score it, since no public benchmark scores word- or char-level location for markdown output.

## Open gaps

- **Not verified by running anything:** whether Docling's *standard* pipeline fills `word_cells`/`char_cells` or only `textline_cells`. The converter sets `_materialize_char_cells` only for `text_cell_unit=CHAR`. Check one datasheet before relying on words or chars.
- **Azure Document Intelligence v4:** spans pointing into the markdown is explicit for Content Understanding but only implied for DI v4 (by the selection-mark note). One trial call would confirm it.
- **Spotting on Zach's current models:** Claude Opus 5.x, Fable and GPT-6 spotting accuracy is not published. The figures above are for Opus 4.6 and GPT-5.2. Measure in the harness before ruling pipeline B out for the CLIs entirely.
- **Benchmark bias and coverage:** ParseBench is block-level and written by the LlamaParse team. Chandra 2's grounding score (about 70 on a third-party snapshot) was not confirmed from a primary source.
- **Not checked:** the Datalab API's claim that "every result traces back to its page and position" (granularity unknown) [41](https://github.com/datalab-to/chandra); and whether Qwen3.5/3.6 kept the `qwenvl html` format.
- **Licences to flag before building on them:**
  - Marker, Surya and Chandra weights: OpenRAIL-M with revenue caps ($5M; $2M for Chandra 2, plus no use competing with Datalab's API).
  - MinerU: its own licence.
  - pymupdf4llm and PyMuPDF: AGPL.
  - Docling, pdfplumber, Defuddle, jsdom, edlib and RapidFuzz are MIT; PaddleOCR is Apache-2.0.
- **Zach's vault recipe is out of date:** it targets marker-pdf 1.10.2, and Marker 2 (2026-07) is a rewrite with new modes, a vLLM server and a different OCR model.

# Source Index

All accessed 2026-10-29. GitHub items were read from source on the default branch via `gh api`.

1. https://github.com/kepano/defuddle (README, v0.19.6)
2. https://github.com/kepano/defuddle/blob/main/src/defuddle.ts
3. https://github.com/kepano/defuddle/blob/main/src/markdown.ts
4. https://github.com/kepano/defuddle/blob/main/src/constants.ts
5. https://github.com/kepano/defuddle/blob/main/src/removals/hidden.ts
6. https://github.com/kepano/defuddle/blob/main/src/removals/scoring.ts
7. https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/highlighter.ts
8. https://github.com/obsidianmd/obsidian-clipper/blob/main/src/utils/interpreter.ts
9. https://github.com/mozilla/readability
10. https://github.com/adbar/trafilatura
11. https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_pdf_converter.py
12. https://github.com/jsdom/jsdom#getting-the-source-location-of-a-node-with-nodelocationnode
13. https://github.com/syntax-tree/hast-util-to-mdast/blob/main/lib/state.js
14. https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py
15. https://github.com/docling-project/docling/blob/main/docling/models/stages/reading_order/readingorder_model.py
16. https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/serializer/base.py
17. https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/serializer/azure.py
18. https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/page.py
19. https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py (and docling/datamodel/base_models.py `Page.parsed_page`)
20. https://github.com/docling-project/docling/blob/main/docling/pipeline/vlm_pipeline.py
21. https://github.com/docling-project/docling/blob/main/docling/pipeline/native_pdf_pipeline.py
22. https://github.com/docling-project/docling/blob/main/docling/backend/html_backend.py
23. https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/items/table/table_data.py
24. https://github.com/datalab-to/marker (README, v2.1.0)
25. https://github.com/datalab-to/marker/blob/master/marker/renderers/json.py
26. https://github.com/datalab-to/marker/blob/master/marker/renderers/html.py
27. https://github.com/datalab-to/marker/blob/master/marker/renderers/ocr_json.py
28. https://github.com/datalab-to/marker/releases (v2.0.0 2026-07-20, v2.1.0 2026-10-29)
29. https://github.com/opendatalab/MinerU (README, 4.1.2)
30. https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md
31. https://github.com/opendatalab/MinerU/blob/master/docs/next/middle-json/current-medium.md
32. https://github.com/opendatalab/MinerU/blob/master/docs/next/decisions/0012-doclib-block-locator.md
33. https://github.com/opendatalab/MinerU/blob/master/mineru/doclib/locators.py
34. https://github.com/allenai/olmocr/blob/main/olmocr/prompts/prompts.py
35. https://github.com/allenai/olmocr/blob/main/olmocr/pipeline.py
36. https://github.com/studio-dots-ai/dots.ocr (README; renamed dots.mocr)
37. https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md
38. https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md
39. https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md
40. https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5
41. https://github.com/datalab-to/chandra (README; olmOCR-bench table; licence)
42. https://github.com/docling-project/docling/blob/main/docling/utils/chandra_utils.py
43. https://github.com/datalab-to/surya (README, Surya 2.5)
44. https://github.com/zai-org/GLM-OCR
45. https://github.com/facebookresearch/nougat
46. https://github.com/Ucas-HaoranWei/GOT-OCR2.0
47. https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/markdown-elements?view=doc-intel-4.0.0
48. https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0
49. https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/elements
50. https://developers.llamaindex.ai/llamaparse/parse/features/layout-and-bounding-boxes/
51. https://developers.llamaindex.ai/llamaparse/parse/examples/parse_granular_bboxes/
52. https://docs.mistral.ai/capabilities/document_ai/basic_ocr
53. https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/documents/elements.py
54. https://docs.reducto.ai/parse/response-format
55. https://github.com/aws-samples/amazon-textract-textractor (textractor/entities/linearizable.py, document.py)
56. https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk
57. https://github.com/pymupdf/pymupdf4llm/blob/main/src/helpers/pymupdf_rag.py
58. https://github.com/jsvine/pdfplumber
59. https://pymupdf.readthedocs.io/en/latest/app1.html
60. https://github.com/mozilla/pdf.js
61. https://github.com/tesseract-ocr/tesseract
62. https://github.com/mindee/doctr
63. https://github.com/QwenLM/Qwen3-VL/tree/main/cookbooks (document_parsing.ipynb, ocr.ipynb)
64. https://ai.google.dev/gemini-api/docs/image-understanding
65. https://huggingface.co/microsoft/Florence-2-large
66. https://github.com/opendatalab/OmniDocBench (README, v1.6_full leaderboard)
67. https://huggingface.co/api/models/{datalab-to/chandra-ocr-2, PaddlePaddle/PaddleOCR-VL-1.6, opendatalab/MinerU2.5-Pro-2604-1.2B, zai-org/GLM-OCR, allenai/olmOCR-2-7B-1025, Qwen/Qwen3-VL-8B-Instruct} (parameter counts, licences)
68. https://arxiv.org/abs/2604.08538 (ParseBench, Table 5)
69. https://arxiv.org/abs/2602.12203 (ExStrucTiny, Table 6)
70. https://99franklin.github.io/ocrbench_v2/ (2026.09 and 2026.03 boards; paper https://arxiv.org/abs/2501.00321)
71. https://github.com/ahnafnafee/local-llm-pdf-ocr/blob/main/src/pdf_ocr/core/aligner.py
72. https://github.com/Martinsos/edlib
73. https://github.com/rapidfuzz/RapidFuzz
74. https://github.com/microsoft/genalog
75. Local check: `/home/bam/.local/bin/codex exec --help` (`-i, --image <FILE>`); `claude --help` (`--input-format stream-json`)
76. https://github.com/run-llama/liteparse (README; points to ParseBench "Visual Grounding")
77. https://github.com/deepseek-ai/DeepSeek-OCR (README; and docling/utils/deepseekocr_utils.py)
78. https://huggingface.co/ibm-granite/granite-docling-258M
