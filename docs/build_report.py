#!/usr/bin/env python3
"""Build Zach's review page for the 2026-10-29 Datasheet Ingestion work: one self-contained HTML file.

    uv run --directory /home/bam/markdownify python docs/build_report.py [--fresh]

Writes reports/media/datasheet-ingestion-2026-10-29.html. WHY reports/media/ (gitignored): the page inlines a video
and images as data URIs, so it opens offline from any folder; the global report rule puts such pages there.
Intermediate files (re-encoded hero, Source Highlighter renders) go to reports/media/datasheet-ingestion-2026-10-29.build/
and are reused on the next run; --fresh redraws them.

The day's recorded demo media (hero video, four screenshots, journey-results.json) are read from
reports/media/datasheet-ingestion-2026-10-29.inputs/, copied there once. WHY a local copy: Markdownify does not
reference any particular host app (CONTRACT §9; its own viewer is `mdify view`), so its code names no outside path
for them.

The 15 "new values" in the human-check section are drawn here, not pasted: each one's grounding is read from the
extractor run that kept it and handed to the Source Highlighter CLI (spotlight), then cropped around the mark.
"""

from __future__ import annotations

import argparse
import base64
import glob
import html
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.parse

from PIL import Image, ImageDraw

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "media" / "datasheet-ingestion-2026-10-29.html"
WORK = REPO / "reports" / "media" / "datasheet-ingestion-2026-10-29.build"

INPUTS = REPO / "reports" / "media" / "datasheet-ingestion-2026-10-29.inputs"
REVIEW_ROUND = pathlib.Path("/home/bam/datasheet-extractor/reports/media/review-view-proposals-2026-10-29")
HIGHLIGHTER = pathlib.Path("/home/bam/source-highlighter")
EXTRACTOR = pathlib.Path("/home/bam/datasheet-extractor")
SAMPLE = EXTRACTOR / "eval" / "results" / "r0-new-sample.json"
R0_RUNS = pathlib.Path.home() / ".cache" / "datasheet-extractor" / "eval" / "r0"
BLOBS = pathlib.Path.home() / ".cache" / "robotparts-ingest" / "blobs"

# WHY these sizes: Zach's desktop preview stops at ~1.9 MB for a media page; JPEG q80 keeps UI text crisp.
SCREENSHOT_WIDTH = 1200
JPEG_QUALITY = 80


# ---------------------------------------------------------------- media helpers

def jpeg_bytes(image: Image.Image, width: int | None = None, quality: int = JPEG_QUALITY) -> bytes:
    image = image.convert("RGB")
    if width and image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def data_uri(data: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = sorted(glob.glob(str(pathlib.Path.home() / ".cache/ms-playwright/ffmpeg-*/ffmpeg-linux")))
    if not candidates:
        sys.exit("ffmpeg not found (PATH or ~/.cache/ms-playwright/ffmpeg-*)")
    return candidates[-1]


def hero_video(fresh: bool) -> bytes:
    """The real demo recording, re-encoded smaller (H.264, 1280 px, CRF 34): 781 KB → ~270 KB, text still legible."""
    source = INPUTS / "hero-markdownify.mp4"
    target = WORK / "hero-1280-crf34.mp4"
    if fresh or not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-i", str(source), "-an",
                        "-vf", "scale=1280:-2:flags=lanczos", "-c:v", "libx264", "-preset", "veryslow",
                        "-tune", "stillimage", "-crf", "34", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        "-r", "25", str(target)], check=True)
    return target.read_bytes()


def hero_poster(fresh: bool) -> bytes:
    """A still of the money shot (350.4W spotlit, ~6.4 s in), shown until the video starts."""
    frame = WORK / "hero-poster.png"
    if fresh or not frame.is_file():
        subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-ss", "6.4", "-i", str(INPUTS / "hero-markdownify.mp4"),
                        "-frames:v", "1", str(frame)], check=True)
    return jpeg_bytes(Image.open(frame), 1280, quality=70)


def screenshot(name: str, width: int = SCREENSHOT_WIDTH) -> tuple[str, int, int]:
    image = Image.open(INPUTS / name)
    data = jpeg_bytes(image, width)
    shown = Image.open(io.BytesIO(data))
    return data_uri(data, "image/jpeg"), shown.width, shown.height


def crop_union(path: pathlib.Path, boxes: list[list[float]], margin: tuple[int, int, int, int],
               width: int | None = None) -> tuple[str, int, int]:
    """Crop an image to the union of normalized boxes plus (left, top, right, bottom) pixel margins."""
    image = Image.open(path)
    w, h = image.size
    x0 = min(b[0] for b in boxes) * w - margin[0]
    y0 = min(b[1] for b in boxes) * h - margin[1]
    x1 = max(b[2] for b in boxes) * w + margin[2]
    y1 = max(b[3] for b in boxes) * h + margin[3]
    region = image.crop((max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1))))
    data = jpeg_bytes(region, width)
    shown = Image.open(io.BytesIO(data))
    return data_uri(data, "image/jpeg"), shown.width, shown.height


def gallery() -> list[dict]:
    """Three real Source Highlighter outputs, cropped to the part of the page that holds the marks."""
    fixtures = HIGHLIGHTER / "tests" / "fixtures"
    out = HIGHLIGHTER / "out"
    j60 = [loc["box"] for loc in json.loads((fixtures / "j60-joint.locations.json").read_text())
           if loc.get("page") == 2] or [loc["box"] for loc in json.loads((fixtures / "j60-joint.locations.json").read_text())]
    erob = [loc["box"] for loc in json.loads((fixtures / "erob80.locations.json").read_text())]
    twin = [loc["box"] for loc in json.loads((fixtures / "cubemars-ak80-9.twin.locations.json").read_text())]
    items = []
    uri, w, h = crop_union(out / "j60-spotlight.png", j60, (140, 150, 120, 470), width=760)
    items.append({"uri": uri, "w": w, "h": h, "title": "Spotlight · PDF",
                  "caption": "DEEP Robotics J60 joint, page 2. Weight and peak torque for both models stay lit; "
                             "the rest of the page is dimmed."})
    uri, w, h = crop_union(out / "erob80-blackout.png", erob, (60, 60, 90, 60))
    items.append({"uri": uri, "w": w, "h": h, "title": "Blackout · web page",
                  "caption": "ZeroErr eRob80 page. Only the row label 额定扭矩 (rated torque), the model and "
                             "its value are left: blackout poetry for a datasheet."})
    uri, w, h = crop_union(out / "cubemars-twin-box.png", twin, (120, 150, 600, 160), width=760)
    items.append({"uri": uri, "w": w, "h": h, "title": "Box · visual twin",
                  "caption": "CubeMars AK80-9, drawn on the screenshot taken at fetch time (the twin), because "
                             "raw HTML alone doesn't render faithfully offline."})
    return items


# ---------------------------------------------------------------- the 15 values, drawn

def resolve(item: dict) -> dict | None:
    """The kept value in the extractor run that produced this sample row: its grounding and source sha256."""
    for directory in sorted((R0_RUNS / item["part"]).glob("input*")):
        for config in item["configs"]:
            option, backend = config.split("/")
            path = directory / f"{option}-{backend}" / "values.json"
            if not path.is_file():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["source"].get("url") != item["source_url"]:
                continue
            for value in record["values"]:
                if value["key"] != item["key"] or value.get("verdict") != "kept" or value["value"] != item["value"]:
                    continue
                if item["cite"] and (value.get("cite") or {}).get("id") != item["cite"]["id"]:
                    continue
                return {"values_json": path, "sha256": record["source"]["sha256"], "grounding": value["grounding"],
                        "config": config}
    return None


def highlighter(source: pathlib.Path, locations: list[dict], style: str, out_png: pathlib.Path) -> dict:
    loc_path = out_png.with_suffix(".locations.json")
    loc_path.write_text(json.dumps(locations), encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}  # WHY: uv run inside uv run
    done = subprocess.run(["uv", "run", "--quiet", "--directory", str(HIGHLIGHTER), "source-highlighter", str(source),
                           "--locations", str(loc_path), "--style", style, "--out", str(out_png)],
                          capture_output=True, text=True, env=environment)
    if done.returncode != 0:
        raise RuntimeError(f"source-highlighter failed on {source}: {done.stderr[-800:]}")
    return json.loads(done.stdout)


def alt_text_fallback(source_text: str, grounding: dict) -> str | None:
    """When a source_range sits inside an <img alt="…">, the longest line of that alt text (to find it printed)."""
    span = grounding.get("html", {}).get("source_range")
    if not span:
        return None
    start = span["start"]
    tag_open = source_text.rfind("<", 0, start)
    if tag_open < 0 or source_text.rfind(">", tag_open, start) != -1:
        return None
    if not source_text[tag_open:start].lower().startswith("<img") or 'alt="' not in source_text[tag_open:start]:
        return None
    alt = source_text[start:span["end"]]
    lines = [line.strip() for line in alt.splitlines() if line.strip()]
    return max(lines, key=len) if lines else None


def stitched(image: Image.Image, top: tuple[int, int], bottom: tuple[int, int], x1: int) -> Image.Image:
    """Header strip + the cited rows, with a dashed band where rows were skipped."""
    a = image.crop((0, top[0], x1, top[1]))
    b = image.crop((0, bottom[0], x1, bottom[1]))
    band = 14
    canvas = Image.new("RGB", (x1, a.height + band + b.height), (226, 226, 230))
    canvas.paste(a.convert("RGB"), (0, 0))
    canvas.paste(b.convert("RGB"), (0, a.height + band))
    draw = ImageDraw.Draw(canvas)
    y = a.height + band // 2
    for x in range(4, x1, 14):
        draw.line([(x, y), (x + 7, y)], fill=(120, 120, 128), width=2)
    return canvas


def human_check_items(fresh: bool) -> list[dict]:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    items = []
    for number, item in enumerate(sample, 1):
        found = resolve(item)
        card = {"n": number, "item": item, "uri": None, "note": None, "stitched": False}
        if not found:
            card["note"] = "No extractor run holds this value any more, so there is nothing to draw."
            items.append(card)
            continue
        source = BLOBS / found["sha256"]
        png = WORK / f"value-{number:02d}.png"
        result_path = WORK / f"value-{number:02d}.result.json"
        locations = [dict(g, id=f"value-{number}-{k}") for k, g in enumerate(found["grounding"])]
        if fresh or not png.is_file() or not result_path.is_file():
            result = highlighter(source, locations, "spotlight", png)
            if not result["marks"] and result["unresolved"]:
                text = source.read_bytes().decode("utf-8", "replace")
                quote = alt_text_fallback(text, found["grounding"][0])
                if quote:
                    result = highlighter(source, [{"id": f"value-{number}-alt", "html": {"text_quote": {"exact": quote}}}],
                                         "spotlight", png)
                    result["fallback"] = {"reason": "alt", "quote": quote}
            result_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        card["fallback"] = result.get("fallback")
        if not result["marks"]:
            card["note"] = "The Source Highlighter could not place this grounding: " + "; ".join(
                u["reason"] for u in result["unresolved"])
            items.append(card)
            continue
        image = Image.open(png)
        width, height = image.size
        rects = [m["rect"] for m in result["marks"]]
        ux0, uy0 = min(r[0] for r in rects), min(r[1] for r in rects)
        ux1, uy1 = max(r[2] for r in rects), max(r[3] for r in rects)
        is_pdf = "page" in found["grounding"][0]
        xpath = (found["grounding"][0].get("html") or {}).get("xpath") or ""
        crop = None
        crop_left = 0
        if item.get("multi_model_table") and "/tbody/tr[" in xpath:
            # WHY a header strip: in a comparison table the column header (the field) is many rows above the value.
            base, tail = xpath.rsplit("/tbody/tr[", 1)
            row = int(tail.split("]")[0])
            cell = tail.split("/", 1)[1]
            header_png = WORK / f"value-{number:02d}-header.png"
            header_result_path = WORK / f"value-{number:02d}-header.result.json"
            if fresh or not header_result_path.is_file():
                header_result = highlighter(source, [{"id": "first-row", "html": {"xpath": f"{base}/tbody/tr[1]/{cell}"}}],
                                            "box", header_png)
                header_result_path.write_text(json.dumps(header_result, indent=1), encoding="utf-8")
                header_png.unlink(missing_ok=True)
            header_result = json.loads(header_result_path.read_text(encoding="utf-8"))
            if header_result["marks"]:
                first_top = header_result["marks"][0]["rect"][1]
                x1 = min(width, 660)
                if row <= 3:
                    crop = image.crop((0, int(first_top - 64), x1, int(uy1 + 26))).convert("RGB")
                else:
                    crop = stitched(image, (int(first_top - 64), int(first_top - 9)), (int(uy0 - 46), int(uy1 + 26)), x1)
                    card["stitched"] = True
        if crop is None:
            target_width = 900 if not is_pdf else 940
            x0 = max(0, min(int(ux0 - 40), int(ux1 + 60 - target_width)))
            x1 = min(width, x0 + target_width)
            crop_left = x0
            above, below = (210, 60) if is_pdf else (112, 70)
            crop = image.crop((x0, max(0, int(uy0 - above)), x1, min(height, int(uy1 + below)))).convert("RGB")
        data = jpeg_bytes(crop)
        shown = Image.open(io.BytesIO(data))
        card["cx"] = round(min(1.0, max(0.0, ((ux0 + ux1) / 2 - crop_left) / crop.width)), 3)
        card.update({"uri": data_uri(data, "image/jpeg"), "w": shown.width, "h": shown.height,
                     "config": found["config"], "values_json": str(found["values_json"])})
        items.append(card)
    return items


# ---------------------------------------------------------------- page text helpers

E = html.escape

SOURCES = [
    ("Markdownify harness scores (12 cases × PDF/PNG/HTML; runs mdify, vlm-claude, vlm-codex, vlm-ab-*)",
     "/home/bam/markdownify/harness/out/results.json"),
    ("Markdownify contract: bundle, span index, ledger, KPIs, capture levels, extractor and RobotParts seams",
     "/home/bam/markdownify/CONTRACT.md"),
    ("Markdownify README: the three pipelines, mdify capture and its measured sizes",
     "/home/bam/markdownify/README.md"),
    ("Coverage ledger over robotparts' 46 cached web pages", "/home/bam/markdownify/scripts/real_html_ledger.py"),
    ("Markdownify commits: 8b1570b rectangular tables, 8b2367f no stitched matches, 417183a visual reading order, "
     "dd8a19c conservative HTML, 5f2d467 pipeline AB, 5471e12 --pipeline on the main command, ffb618f value ids + "
     "review.json (CONTRACT §7, §7.1), 631ec5d Markdownify's own viewer, no Clank (CONTRACT §9)",
     "git -C /home/bam/markdownify log"),
    ("Datasheet Extractor results: Option 1 vs 2, both models, synthetic set and R0",
     "/home/bam/datasheet-extractor/eval/RESULTS.md"),
    ("The 15 sampled new values, and the extractor run that kept each one",
     "/home/bam/datasheet-extractor/eval/results/r0-new-sample.json"),
    ("JMC 60ASM200 first job (private supplier list, outside git)",
     "~/.cache/datasheet-extractor/fixtures/jmc-60asm200-selection-list-2026-10-29/"),
    ("Prototype viewer journey: 23 of 23 checks, and the hero recording (copied into this page's inputs folder)",
     "/home/bam/markdownify/reports/media/datasheet-ingestion-2026-10-29.inputs/journey-results.json"),
    ("Source Highlighter README and contract; 9e87a0e resolves HTML exactly, never by first occurrence",
     "/home/bam/source-highlighter/README.md"),
    ("Prior-art research note (3 lanes, 187 sources) and the pipeline canvas",
     "/home/bam/zach_brain/Projects/Markdownify/Markdownify — Anything to Markdown with a Source Map, Prior Art (2026-10-29).md"),
    ("The orchestrator's verified fact sheet for this report: numbers with no repo file of their own (ruler "
     "self-check and tests, CubeMars shared-file sizes, RobotParts' vocab response)", "session scratchpad REPORT-FACTS.md"),
    ("This page's own Source Highlighter runs (the 15 crops), made by docs/build_report.py",
     "/home/bam/markdownify/reports/media/datasheet-ingestion-2026-10-29.build/"),
]


def c(*numbers: int) -> str:
    """An inline citation: bare numbers, comma-joined, linking to the Source Index."""
    links = ",".join(f'<a href="#src-{n}">{n}</a>' for n in numbers)
    return f'<sup class="cite">{links}</sup>'


def table(head: list[str], rows: list[list[str]], numeric_from: int = 1, caption: str = "") -> str:
    out = ['<div class="tw"><table>']
    if caption:
        out.append(f"<caption>{caption}</caption>")
    out.append("<thead><tr>" + "".join(f"<th{' class=\"num\"' if i >= numeric_from else ''}>{h}</th>"
                                        for i, h in enumerate(head)) + "</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td{' class=\"num\"' if i >= numeric_from else ''}>{cell}</td>"
                                    for i, cell in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def command(line: str, label: str) -> str:
    return (f'<div class="cmd"><div class="cmd-label">{label}</div><div class="cmd-row"><pre><code>{E(line)}</code></pre>'
            f'<button class="copy" type="button" data-copy="{E(line)}">Copy</button></div></div>')


def value_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def brand(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc
    if "cubemars" in host:
        return "CubeMars"
    if "deep-website" in host or "deeprobotics" in host:
        return "DEEP Robotics"
    return host


def value_id(item: dict) -> str:
    """CONTRACT §7's id: <key>@<cite id>, or <key>@html / @p<page> when the value has no cite."""
    if item.get("cite"):
        return f"{item['key']}@{item['cite']['id']}"
    return f"{item['key']}@html" if item["source_url"].lower().endswith((".html", "/")) or "html" in item["source_url"] \
        else f"{item['key']}@p1"


# ---------------------------------------------------------------- the diagram

DIAGRAM = """
<svg class="diagram" viewBox="0 0 1000 500" role="img" aria-labelledby="dg-title dg-desc" xmlns="http://www.w3.org/2000/svg">
  <title id="dg-title">The four black boxes and what flows between them</title>
  <desc id="dg-desc">Capture feeds the raw source. Raw source goes to Markdownify, whose bundle feeds the Datasheet Extractor (Option 1); Option 2 sends the raw source straight to the extractor. The extractor's values go to RobotParts. Groundings and the stored source go to the Source Highlighter, which draws marks for the viewer (mdify view) and RobotParts' Go to source.</desc>
  <defs>
    <marker id="ar-m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-m"/></marker>
    <marker id="ar-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-a"/></marker>
    <marker id="ar-w" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-w"/></marker>
  </defs>

  <!-- edges first, boxes on top -->
  <path class="e" d="M95,118 L95,176" marker-end="url(#ar-m)"/>
  <text class="el" x="88" y="150" text-anchor="end">kept levels</text>

  <path class="e" d="M170,215 L256,215" marker-end="url(#ar-m)"/>
  <text class="el" x="178" y="206">bytes</text>
  <text class="el" x="178" y="232">sha256</text>

  <path class="e e-a" d="M450,215 L536,215" marker-end="url(#ar-a)"/>
  <text class="el el-a" x="456" y="200">Option 1</text>
  <text class="el" x="456" y="236">bundle +</text>
  <text class="el" x="456" y="250">cell ids</text>

  <path class="e e-w" d="M130,176 L130,140 Q130,128 142,128 L600,128 Q612,128 612,140 L612,174" marker-end="url(#ar-w)"/>
  <text class="el el-w" x="262" y="120">Option 2: raw source only; the model quotes text and draws a box</text>

  <path class="e" d="M730,215 L816,215" marker-end="url(#ar-m)"/>
  <text class="el" x="738" y="206">values.json</text>
  <text class="el" x="738" y="234">value · unit</text>
  <text class="el" x="738" y="248">raw · cite</text>
  <text class="el" x="738" y="262">grounding</text>

  <path class="e" d="M650,256 L650,336" marker-end="url(#ar-m)"/>
  <text class="el" x="658" y="292">groundings</text>
  <text class="el" x="658" y="306">= Locations</text>

  <path class="e" d="M60,256 L60,470 Q60,478 68,478 L600,478 Q612,478 612,470 L612,424" marker-end="url(#ar-m)"/>
  <text class="el" x="220" y="470">the stored source (PDF, image, archived page)</text>

  <path class="e" d="M536,380 L454,380" marker-end="url(#ar-m)"/>
  <text class="el" x="462" y="372">marks</text>

  <path class="e" d="M355,256 L355,336" marker-end="url(#ar-m)"/>
  <text class="el" x="363" y="292">bundle</text>

  <path class="e" d="M732,372 Q900,372 900,262" marker-end="url(#ar-m)"/>
  <text class="el" x="908" y="318">“Go to</text><text class="el" x="908" y="332">source”</text>

  <!-- boxes -->
  <g class="b b-new"><rect x="20" y="48" width="150" height="68" rx="10"/>
    <text class="bt" x="95" y="76">Capture</text><text class="bs" x="95" y="96">mdify capture</text></g>
  <g class="b b-src"><rect x="20" y="178" width="150" height="76" rx="10"/>
    <text class="bt" x="95" y="208">Raw source</text><text class="bs" x="95" y="228">PDF · web page</text><text class="bs" x="95" y="243">image</text></g>
  <g class="b b-new"><rect x="258" y="178" width="192" height="76" rx="10"/>
    <text class="bt" x="354" y="206">Markdownify</text><text class="bs" x="354" y="226">markdown + span index</text><text class="bs" x="354" y="241">+ coverage ledger</text></g>
  <g class="b b-new"><rect x="538" y="178" width="192" height="76" rx="10"/>
    <text class="bt" x="634" y="206">Datasheet Extractor</text><text class="bs" x="634" y="226">extract --option 1|2</text><text class="bs" x="634" y="241">+ checker</text></g>
  <g class="b b-old"><rect x="818" y="178" width="162" height="80" rx="10"/>
    <text class="bt" x="899" y="206">RobotParts</text><text class="bs" x="899" y="226">part.json</text><text class="bs" x="899" y="241">existing</text></g>
  <g class="b b-new"><rect x="538" y="338" width="192" height="84" rx="10"/>
    <text class="bt" x="634" y="366">Source Highlighter</text><text class="bs" x="634" y="386">source + Locations + style</text><text class="bs" x="634" y="401">→ marked source</text></g>
  <g class="b b-new"><rect x="258" y="338" width="194" height="84" rx="10"/>
    <text class="bt" x="355" y="366">Viewer</text><text class="bs" x="355" y="386">markdown ↔ source, values</text><text class="bs" x="355" y="401">→ mdify view, its own page</text></g>

  <!-- legend -->
  <g class="lg" transform="translate(700,40)">
    <rect class="lg-new" x="0" y="0" width="22" height="14" rx="3"/><text class="el" x="30" y="11">new today, local repo</text>
    <rect class="lg-old" x="0" y="22" width="22" height="14" rx="3"/><text class="el" x="30" y="33">existing</text>
    <line class="e e-a" x1="160" y1="7" x2="190" y2="7"/><text class="el" x="196" y="11">Option 1</text>
    <line class="e e-w" x1="160" y1="29" x2="190" y2="29"/><text class="el" x="196" y="33">Option 2</text>
  </g>
</svg>
"""


def offset_bar(label: str, header: tuple[float, float], value: float, inside: bool, y: int) -> str:
    """One row of the to-scale header/value strip (x in the viewer's rendered markdown, px; axis 1050–1350)."""
    def sx(x: float) -> float:
        return 140 + (x - 1050) * (520 / 300)
    colour = "ok" if inside else "bad"
    return (f'<text class="el" x="0" y="{y + 15}">{label}</text>'
            f'<rect class="hdr" x="{sx(header[0]):.1f}" y="{y}" width="{sx(header[1]) - sx(header[0]):.1f}" height="22" rx="4"/>'
            f'<text class="el hdr-t" x="{(sx(header[0]) + sx(header[1])) / 2:.1f}" y="{y + 15}" text-anchor="middle">LRS-350-24</text>'
            f'<circle class="dot {colour}" cx="{sx(value):.1f}" cy="{y + 40}" r="6"/>'
            f'<text class="el" x="{sx(value) + 10:.1f}" y="{y + 44}">350.4W centre {value}</text>')


def shift_svg() -> str:
    ticks = "".join(f'<line class="tick" x1="{140 + (x - 1050) * 520 / 300:.1f}" y1="150" x2="{140 + (x - 1050) * 520 / 300:.1f}" y2="156"/>'
                    f'<text class="el" x="{140 + (x - 1050) * 520 / 300:.1f}" y="170" text-anchor="middle">{x}</text>'
                    for x in (1100, 1200, 1300))
    return ('<svg class="shift" viewBox="0 0 700 180" role="img" aria-label="Before: the 350.4W cell centre (1206.3) '
            'falls outside the LRS-350-24 header (1097.8–1170.1). After: 1293.3 sits inside 1257.1–1329.5.">'
            + offset_bar("Before", (1097.8, 1170.1), 1206.3, False, 10)
            + offset_bar("After", (1257.1, 1329.5), 1293.3, True, 82)
            + '<line class="axis" x1="140" y1="150" x2="660" y2="150"/>' + ticks
            + '</svg>')


# ---------------------------------------------------------------- page

CSS = r"""
:root{--bg:#fbfbfa;--surface:#fff;--surface-2:#f3f3f0;--ink:#1c1c1e;--muted:#5f5f67;--line:#e3e3df;--accent:#2f5fd0;
--accent-soft:#eaf0fd;--good:#1d7a4b;--good-soft:#e2f3e9;--bad:#b3261e;--bad-soft:#fbe8e6;--warn:#8a5d00;--warn-soft:#fbf0d9;
--code:#f3f3f0;--hdr:#d9e3fb;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#131315;--surface:#1b1b1e;--surface-2:#232327;
--ink:#ececef;--muted:#a3a3ab;--line:#34343a;--accent:#8fb0ff;--accent-soft:#1d2741;--good:#63d39c;--good-soft:#15331f;
--bad:#ff8f86;--bad-soft:#3c1b19;--warn:#f0c46a;--warn-soft:#382d13;--code:#1f1f23;--hdr:#2a3a63;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#131315;--surface:#1b1b1e;--surface-2:#232327;--ink:#ececef;--muted:#a3a3ab;--line:#34343a;
--accent:#8fb0ff;--accent-soft:#1d2741;--good:#63d39c;--good-soft:#15331f;--bad:#ff8f86;--bad-soft:#3c1b19;--warn:#f0c46a;
--warn-soft:#382d13;--code:#1f1f23;--hdr:#2a3a63;color-scheme:dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","Noto Sans CJK SC",sans-serif}
main{max-width:960px;margin:0 auto;padding:0 16px 80px}
header.top{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;padding:28px 0 8px}
h1{font-size:26px;line-height:1.25;margin:0}
.sub{color:var(--muted);margin:4px 0 0;font-size:14.5px}
h2{font-size:21px;margin:44px 0 10px;padding-top:18px;border-top:1px solid var(--line)}
h2 .n{color:var(--muted);font-weight:500;margin-right:6px}
h3{font-size:16.5px;margin:26px 0 6px}
p{margin:8px 0}
ul{margin:6px 0;padding-left:22px}
li{margin:3px 0}
a{color:var(--accent)}
code{font:13.5px/1.45 ui-monospace,SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;background:var(--code);padding:1px 5px;border-radius:5px;overflow-wrap:anywhere}
.muted{color:var(--muted)}
.small{font-size:14px}
sup.cite{font-size:11px;line-height:0;margin-left:1px}
sup.cite a{text-decoration:none;color:var(--muted)}
sup.cite a:hover{color:var(--accent)}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:14px;margin:10px 0 0;padding:10px 0;border-top:1px solid var(--line)}
nav.toc a{text-decoration:none;color:var(--muted)}
nav.toc a:hover{color:var(--accent)}
.verdict{background:var(--accent-soft);border-left:4px solid var(--accent);padding:14px 16px;border-radius:8px;margin:18px 0 10px}
.verdict p.v{font-weight:700;font-size:17.5px;margin:0 0 4px;line-height:1.4}
.verdict p.w{margin:0;color:var(--muted);font-size:14.5px}
.highlights li{margin:6px 0}
.theme{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:6px 10px;font:inherit;font-size:13px;cursor:pointer;white-space:nowrap}
figure{margin:14px 0}
figure img,figure video{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)}
figcaption{color:var(--muted);font-size:14px;margin-top:6px}
.tw{overflow-x:auto;margin:10px 0;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14.5px}
caption{text-align:left;padding:10px 12px 0;color:var(--muted);font-size:13.5px}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{font-weight:600;background:var(--surface-2);white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.win td{background:var(--good-soft)}
.defs{font-size:14px;color:var(--muted)}
.defs b{color:var(--ink);font-weight:600}
.callout{border:1px solid var(--line);background:var(--surface);border-radius:10px;padding:12px 14px;margin:12px 0}
.callout.new{border-color:var(--warn);background:var(--warn-soft)}
.diagram-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:8px}
svg.diagram{display:block;min-width:760px;width:100%;height:auto}
svg .b rect{fill:var(--surface-2);stroke:var(--ink);stroke-width:1.6}
svg .b-old rect{stroke-dasharray:6 5;stroke:var(--muted)}
svg .b-src rect{fill:var(--surface);stroke:var(--muted)}
svg .bt{font:600 15px system-ui,sans-serif;fill:var(--ink);text-anchor:middle}
svg .bs{font:12.5px system-ui,sans-serif;fill:var(--muted);text-anchor:middle}
svg .el{font:12px system-ui,sans-serif;fill:var(--muted)}
svg .el-a{fill:var(--accent);font-weight:600}
svg .el-w{fill:var(--warn);font-weight:600}
svg .e{fill:none;stroke:var(--muted);stroke-width:1.5}
svg .e-a{stroke:var(--accent);stroke-width:2.2}
svg .e-w{stroke:var(--warn);stroke-width:1.8;stroke-dasharray:6 5}
svg .ah-m{fill:var(--muted)} svg .ah-a{fill:var(--accent)} svg .ah-w{fill:var(--warn)}
svg .lg-new{fill:var(--surface-2);stroke:var(--ink);stroke-width:1.4}
svg .lg-old{fill:none;stroke:var(--muted);stroke-dasharray:4 3;stroke-width:1.4}
svg.shift{display:block;width:100%;max-width:700px;height:auto}
svg.shift .hdr{fill:var(--hdr)}
svg.shift .hdr-t{fill:var(--ink)}
svg.shift .dot.ok{fill:var(--good)} svg.shift .dot.bad{fill:var(--bad)}
svg.shift .axis,svg.shift .tick{stroke:var(--muted);stroke-width:1}
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;align-items:start}
.gallery figure{margin:0}
.shots{display:grid;gap:18px}
.cards{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;margin-top:12px}
.card{min-width:0;border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:14px}
.card.ans-yes{border-color:var(--good);box-shadow:inset 4px 0 0 var(--good)}
.card.ans-no{border-color:var(--bad);box-shadow:inset 4px 0 0 var(--bad)}
.card.ans-unsure{border-color:var(--warn);box-shadow:inset 4px 0 0 var(--warn)}
.card-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.card-head .num{color:var(--muted);font-variant-numeric:tabular-nums;font-weight:600}
.card-head .what{font-weight:650;font-size:16.5px}
.card-head .val{font-weight:700;font-size:18px}
.facts{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;font-size:14px;margin:8px 0 10px}
.facts dt{color:var(--muted)}
.facts dd{margin:0;overflow-wrap:anywhere}
.shot{overflow-x:auto;border-radius:6px;border:1px solid var(--line)}
.shot img{display:block;max-width:100%;height:auto}
.hint{display:none;font-size:13px;color:var(--muted);margin:4px 0 0}
.card .why{font-size:13.5px;color:var(--muted);margin:6px 0 0}
.answer{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.answer button{font:inherit;font-size:15px;min-width:52px;padding:7px 12px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);color:var(--ink);cursor:pointer}
.answer button[aria-pressed="true"].yes{background:var(--good);border-color:var(--good);color:#fff}
.answer button[aria-pressed="true"].no{background:var(--bad);border-color:var(--bad);color:#fff}
.answer button[aria-pressed="true"].unsure{background:var(--warn);border-color:var(--warn);color:#fff}
.answer input{flex:1 1 200px;min-width:0;font:inherit;font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.tally{position:sticky;bottom:0;z-index:2;margin-top:16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 14px;box-shadow:0 -4px 16px rgba(0,0,0,.06)}
.tally button{font:inherit;font-weight:600;padding:8px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
#answers-out{width:100%;min-height:120px;margin-top:10px;font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--line);border-radius:8px;background:var(--code);color:var(--ink);padding:10px;display:none}
.decide{display:grid;gap:12px}
.ask{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:12px 14px}
.ask .q{font-weight:650}
.ask .rec,.ask .def{font-size:14.5px;margin:4px 0 0}
.ask .rec b{color:var(--good)}
.ask .def b{color:var(--muted)}
.cmd{margin:10px 0}
.cmd-label{font-size:14.5px;margin-bottom:4px}
.cmd-row{display:flex;gap:8px;align-items:stretch}
.cmd pre{flex:1;min-width:0;margin:0;overflow-x:auto;background:var(--code);border:1px solid var(--line);border-radius:8px;padding:9px 12px}
.cmd pre code{background:none;padding:0;white-space:pre;overflow-wrap:normal}
button.copy{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:0 12px;cursor:pointer}
ol.sources{font-size:13.5px;color:var(--muted);padding-left:26px}
ol.sources li{margin:5px 0}
ol.sources code{font-size:12.5px}
@media (max-width:700px){.shot img{max-width:none;width:680px}.hint{display:block}}
@media (max-width:560px){h1{font-size:22px}h2{font-size:19px}.verdict p.v{font-size:16px}th,td{padding:7px 9px}
.facts{grid-template-columns:1fr}.facts dt{margin-top:4px}header.top{flex-direction:column}}
"""

JS = r"""
(function(){
  var THEME_KEY='datasheet-ingestion-2026-10-29/theme';
  var KEY='datasheet-ingestion-2026-10-29/human-check@1';
  function get(k){try{return window.localStorage.getItem(k)}catch(e){return null}}
  function set(k,v){try{window.localStorage.setItem(k,v);return true}catch(e){return false}}
  var root=document.documentElement, themeBtn=document.getElementById('theme');
  function applyTheme(t){if(t==='light'||t==='dark'){root.setAttribute('data-theme',t)}else{root.removeAttribute('data-theme')}
    if(themeBtn){themeBtn.textContent='Theme: '+(t||'auto')}}
  var theme=get(THEME_KEY); applyTheme(theme);
  if(themeBtn){themeBtn.addEventListener('click',function(){theme=theme==='light'?'dark':(theme==='dark'?null:'light');
    applyTheme(theme); if(theme){set(THEME_KEY,theme)}else{try{window.localStorage.removeItem(THEME_KEY)}catch(e){}}})}

  var state={};
  try{state=JSON.parse(get(KEY)||'{}')||{}}catch(e){state={}}
  var storageOk=set(KEY+'/probe','1');
  var cards=[].slice.call(document.querySelectorAll('.card[data-id]'));
  var SYMBOL={yes:'✓',no:'✗',unsure:'?'};
  function save(){set(KEY,JSON.stringify(state))}
  function render(){
    var counts={yes:0,no:0,unsure:0};
    cards.forEach(function(card){
      var s=state[card.dataset.id]||{};
      card.classList.remove('ans-yes','ans-no','ans-unsure');
      if(s.v){card.classList.add('ans-'+s.v);counts[s.v]++}
      [].forEach.call(card.querySelectorAll('.answer button'),function(b){b.setAttribute('aria-pressed',String(b.dataset.v===s.v))});
      var input=card.querySelector('.answer input'); if(input&&document.activeElement!==input){input.value=s.note||''}
    });
    var done=counts.yes+counts.no+counts.unsure;
    var t=document.getElementById('tally-text');
    if(t){t.textContent=done+' of '+cards.length+' answered · ✓ '+counts.yes+' · ✗ '+counts.no+' · ? '+counts.unsure+(storageOk?'':' · (this browser blocks storage: answers last until you close the page)')}
  }
  cards.forEach(function(card){
    [].forEach.call(card.querySelectorAll('.answer button'),function(b){b.addEventListener('click',function(){
      var s=state[card.dataset.id]||{}; s.v=(s.v===b.dataset.v)?undefined:b.dataset.v; state[card.dataset.id]=s; save(); render();})});
    var input=card.querySelector('.answer input');
    if(input){input.addEventListener('input',function(){var s=state[card.dataset.id]||{}; s.note=input.value; state[card.dataset.id]=s; save(); render();})}
  });
  function answersText(){
    var lines=['Human check · Datasheet Ingestion 2026-10-29 · 15 new R0 values (datasheet-extractor/eval/results/r0-new-sample.json)'];
    var missing=[], counts={yes:0,no:0,unsure:0};
    cards.forEach(function(card){
      var s=state[card.dataset.id]||{}, d=card.dataset;
      if(s.v){counts[s.v]++} else {missing.push(d.n)}
      lines.push((d.n.length<2?' ':'')+d.n+' '+(s.v?SYMBOL[s.v]:'·')+' '+d.model+' · '+d.key+' = '+d.value+(d.unit?' '+d.unit:'')+' · '+d.vid+' · '+d.part+(s.note?' — '+s.note.replace(/\s+/g,' ').trim():''));
    });
    lines.push('answered '+(cards.length-missing.length)+' of '+cards.length+' · ✓ '+counts.yes+' · ✗ '+counts.no+' · ? '+counts.unsure+(missing.length?' · unanswered: '+missing.join(', '):''));
    return lines.join('\n');
  }
  var copyBtn=document.getElementById('copy-answers'), out=document.getElementById('answers-out'), status=document.getElementById('copy-status');
  function fallbackCopy(text){out.style.display='block';out.value=text;out.focus();out.select();
    var ok=false;try{ok=document.execCommand('copy')}catch(e){ok=false}return ok}
  if(copyBtn){copyBtn.addEventListener('click',function(){
    var text=answersText(); out.value=text; out.style.display='block';
    function done(ok){status.textContent=ok?'Copied. Paste it back into the chat.':'Clipboard blocked here: the text is selected below, copy it by hand.'}
    try{
      if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(function(){done(true)},function(){done(fallbackCopy(text))})}
      else{done(fallbackCopy(text))}
    }catch(e){done(fallbackCopy(text))}
  })}
  [].forEach.call(document.querySelectorAll('button.copy'),function(b){b.addEventListener('click',function(){
    var text=b.getAttribute('data-copy');
    function mark(ok){b.textContent=ok?'Copied':'Select it';setTimeout(function(){b.textContent='Copy'},1600)}
    try{navigator.clipboard.writeText(text).then(function(){mark(true)},function(){mark(false)})}catch(e){mark(false)}
  })});
  // WHY: on a phone the crops keep a readable size and scroll sideways; start each one centred on its mark.
  function centreShots(){[].forEach.call(document.querySelectorAll('.shot[data-cx]'),function(d){
    if(d.scrollWidth>d.clientWidth){d.scrollLeft=Math.max(0,parseFloat(d.dataset.cx)*d.scrollWidth-d.clientWidth/2)}})}
  window.addEventListener('load',centreShots); window.addEventListener('resize',centreShots);
  [].forEach.call(document.querySelectorAll('.shot img'),function(i){i.addEventListener('load',centreShots)});
  render();
  window.__answersText=answersText;
})();
"""


def card_html(card: dict) -> str:
    item = card["item"]
    number = card["n"]
    unit = item["unit"] or ""
    shown_value = value_text(item["value"])
    cite = item.get("cite") or {}
    cite_bits = []
    if cite.get("id"):
        cite_bits.append(f"<code>{E(cite['id'])}</code>")
    if cite.get("row"):
        cite_bits.append(f"row “{E(cite['row'])}”")
    if cite.get("column"):
        cite_bits.append(f"column “{E(cite['column'])}”")
    if not cite and item.get("quote"):
        cite_bits.append("no cell id: Option 2's quote")
    found_by = ", ".join(item["configs"])
    vid = value_id(item)
    facts = [
        ("Raw (as printed)", f"<code>{E(item['raw'])}</code>"),
        ("Cited", " · ".join(cite_bits) or "—"),
        ("Found by", E(found_by) + (" · comparison table" if item.get("multi_model_table") else "")),
        ("Source", f'<a href="{E(item["source_url"])}" rel="noreferrer noopener">{E(urllib.parse.unquote(item["source_url"]))}</a>'),
        ("Value id", f"<code>{E(vid)}</code> in <code>{E(item['part'])}</code>"),
    ]
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    if card.get("uri"):
        alt = f"Spotlight on the source where {item['key']} = {shown_value} {unit} was found"
        figure = (f'<div class="shot" data-cx="{card.get("cx", 0.5)}"><img src="{card["uri"]}" width="{card["w"]}" '
                  f'height="{card["h"]}" alt="{E(alt)}" loading="lazy"></div>')
    else:
        figure = f'<p class="why">{E(card["note"] or "Nothing to draw.")}</p>'
    whys = []
    if card.get("stitched"):
        whys.append("Top strip: the table's header row. Below the dashed band: the cited row (rows in between cut).")
    if card.get("fallback"):
        whys.append("The extractor's grounding points at this image's <code>alt</code> text, which is an attribute, not "
                    "page text, so the Source Highlighter correctly refused to draw it. Shown instead: the same words "
                    f"where the page prints them under the image (found by an exact, single-match quote: "
                    f"“{E(card['fallback']['quote'])}”).")
    why_html = "".join(f'<p class="why">{w}</p>' for w in whys)
    return (f'<article class="card" id="hc-{number}" data-id="{E(vid)}|{E(item["part"])}" data-n="{number}" '
            f'data-model="{E(item["model"])}" data-key="{E(item["key"])}" data-value="{E(shown_value)}" '
            f'data-unit="{E(unit)}" data-vid="{E(vid)}" data-part="{E(item["part"])}">'
            f'<div class="card-head"><span class="num">#{number}</span>'
            f'<span class="what">{E(brand(item["source_url"]))} {E(item["model"])} · {E(item["key"])}</span>'
            f'<span class="val">{E(shown_value)}{(" " + E(unit)) if unit else ""}</span></div>'
            f'<dl class="facts">{facts_html}</dl>{figure}{why_html}'
            f'<div class="answer" role="group" aria-label="Is #{number} right?">'
            f'<button type="button" class="yes" data-v="yes" aria-pressed="false" title="Right">✓</button>'
            f'<button type="button" class="no" data-v="no" aria-pressed="false" title="Wrong">✗</button>'
            f'<button type="button" class="unsure" data-v="unsure" aria-pressed="false" title="Not sure">?</button>'
            f'<input type="text" placeholder="Why? (optional)" aria-label="Note for #{number}"></div></article>')


def build(fresh: bool) -> pathlib.Path:
    WORK.mkdir(parents=True, exist_ok=True)
    hero = hero_video(fresh)
    poster = hero_poster(fresh)
    shot1 = screenshot("01-markdownify-word-to-source.png")
    shot2 = screenshot("02-datasheet-values.png")
    shot3 = screenshot("03-ledger-detail-tps54331.png")
    shot4 = screenshot("04-markdownify-html.png")
    shots_gallery = gallery()
    cards = human_check_items(fresh)
    journey = json.loads((INPUTS / "journey-results.json").read_text(encoding="utf-8"))["results"]
    journey_ok = sum(1 for r in journey if r["ok"])
    unresolved_alt = sum(1 for card in cards if card.get("fallback"))

    def fig(shot: tuple[str, int, int], alt: str, caption: str) -> str:
        uri, w, h = shot
        return (f'<figure><img src="{uri}" width="{w}" height="{h}" alt="{E(alt)}" loading="lazy">'
                f"<figcaption>{caption}</figcaption></figure>")

    parts: list[str] = []
    add = parts.append

    # 1 · verdict
    add('<header class="top"><div><h1>Datasheet Ingestion</h1>'
        '<p class="sub">Review packet · 2026-10-29 · Markdownify, Datasheet Extractor, Source Highlighter, viewer</p></div>'
        '<button class="theme" id="theme" type="button">Theme: auto</button></header>')
    add('<nav class="toc" aria-label="Sections">'
        + "".join(f'<a href="#{a}">{t}</a>' for a, t in [
            ("verdict", "Verdict"), ("hero", "Video"), ("boxes", "Black boxes"), ("markdownify", "Markdownify"),
            ("bugs", "Bugs caught"), ("extractor", "Extractor"), ("capture", "Capture"),
            ("highlighter", "Highlighter"), ("human-check", "Human check"), ("decide", "Decisions"),
            ("run", "Run it"), ("sources", "Sources")])
        + "</nav>")
    add('<section id="verdict"><div class="verdict">'
        '<p class="v">Any digital datasheet now becomes markdown where every word links back to its spot in the source, '
        'and an extractor keeps only values that code can prove from a cited table cell.</p>'
        '<p class="w">Four new local repos, all on <code>main</code>, nothing pushed (no remotes). Digital sources '
        'only; scans, plots and diagrams are not converted yet.</p></div>'
        '<ul class="highlights">'
        f'<li><b>AB placed every word.</b> The model writes the markdown, the source\'s own words place it: 921 of 921 '
        f'truth words in the right spot on PDF, 920 on PNG. A, with no AI, gets 0.982 / 0.893 at about 1 s a page.{c(1)}</li>'
        f'<li><b>Real web pages: coverage 0.510 → 0.987.</b> The shipped reading mode was silently dropping whole spec '
        f'tables; the coverage number caught it.{c(4, 5)}</li>'
        f'<li><b>Cite the cell, not a quote.</b> Claude read about 98% of the values right under either option (Codex, under Option 2, proposed only 72%). Option 1 can prove 98% of the '
        f'fields (HTML, PDF); Option 2 about a third.{c(6)}</li>'
        f'<li><b>The KPIs caught five real bugs</b>, worst a table header shifted one column that put 350.4W under '
        f'the wrong model.{c(5, 12)}</li>'
        f'<li><b>Your move:</b> seven decisions with defaults, and 15 new values to tick (✓ / ✗ / ?) in '
        f'<a href="#human-check">section 9</a>.</li></ul></section>')

    # 2 · hero
    add('<section id="hero"><h2><span class="n">2</span>The real thing, working</h2>'
        '<figure><video autoplay muted loop playsinline controls preload="auto" width="1280" height="800" '
        'aria-label="The prototype viewer: open a PDF with Markdownify, click a word, the PDF spotlights it" '
        f'poster="{data_uri(poster, "image/jpeg")}">'
        f'<source src="{data_uri(hero, "video/mp4")}" type="video/mp4">'
        'Your viewer cannot play this video; the screenshots below show the same flow.</video>'
        '<figcaption>12.4 s, muted, a real recording of the prototype viewer running inside the real Clank app. Open the Mean Well LRS-350 PDF → '
        '<i>Open with… → Markdownify</i> → it reads the file (4.5 s cold) and shows the KPI strip (coverage 100%, '
        f'traceability 100%) → click <code>350.4W</code> in the markdown and the PDF dims everything but that cell → '
        f'click <code>88%</code>, same.{c(9)}</figcaption></figure></section>')

    # 3 · black boxes
    add('<section id="boxes"><h2><span class="n">3</span>The four black boxes</h2>'
        '<p>Each box is its own repo with a written contract, so you can forget its insides. What crosses each arrow is '
        f'all the next box knows.{c(2, 10)}</p>'
        f'<div class="diagram-wrap">{DIAGRAM}</div><p class="hint">Scroll the diagram sideways.</p>'
        '<ul class="small">'
        '<li><b>Markdownify</b> <code>/home/bam/markdownify</code> — <code>mdify</code>: any PDF, web page or image → '
        'markdown, plus a <i>span index</i> (every word → its page and box, or its character range in the HTML) and a '
        '<i>coverage ledger</i> (every source word: linked, or dropped with a reason). <code>mdify capture</code> lives here too.</li>'
        '<li><b>Datasheet Extractor</b> <code>/home/bam/datasheet-extractor</code> — a field list in, checked '
        '<code>values.json</code> out. Option 1 reads the Markdownify bundle and cites cell ids; Option 2 reads the raw '
        'source only and quotes text.</li>'
        '<li><b>Source Highlighter</b> <code>/home/bam/source-highlighter</code> — source + locations + style in, '
        'marked source out. Its contract owns the Location format everyone else writes.</li>'
        '<li><b>Viewer</b> — the Markdownify view and the Datasheet values view. Recorded today as a Clank plugin '
        '(<code>/home/bam/clank-markdownify</code>); <b>moving out of Clank into <code>mdify view</code>, a standalone '
        'page Markdownify ships itself.</b> Markdownify will not reference Clank (CONTRACT §9, 631ec5d). The video and '
        'screenshots below are that recorded prototype.</li></ul>'
        f'<p class="small muted">Why build at all: no open-source converter maps its own markdown back to word or '
        f'character locations (Docling, Marker and MinerU stop at blocks); Azure Content Understanding and LandingAI do '
        f'it as hosted services. So we copied LandingAI\'s span-index format and built the one missing piece, the '
        f'aligner. W3C Web Annotation was rejected for the internal format: none of 13 extraction tools use it.{c(11)}</p>'
        '</section>')

    # 4 · markdownify
    add('<section id="markdownify"><h2><span class="n">4</span>Markdownify</h2>'
        '<h3>The ruler</h3>'
        f'<p>Your “FK/IK on markdown” idea, built: write ground-truth markdown, render it to PDF, PNG and HTML, run a '
        f'pipeline, compare. 12 datasheet-style cases × 3 forms. Self-check: every character Chrome draws matches its '
        f'PDF glyph, max x error 0.000 pt. 49 ruler tests pass; planted mistakes (shift a box, drop a word, swap cells, '
        f'invent a word, wrong model column) each lower the right score. The truth itself scores perfect on all 36 runs.{c(1, 12)}</p>'
        '<h3>Three pipelines on the ruler (921 truth words)</h3>')
    add(table(["Pipeline", "Form", "Text NED ↓", "TEDS ↑", "Header paths ↑", "Words correctly placed ↑", "s / page"], [
        ["<b>A</b> — parse + align, no AI", "PDF", "0.042", "1.00", "1.00", "904 (0.982)", "12.9 cold (≈1 s warm)"],
        ["A", "PNG", "0.057", "0.99", "0.847", "822 (0.893)", "16.0 cold"],
        ["<b>B</b> — Claude writes markdown + line boxes", "PDF / PNG", "0.000", "1.00", "1.00", "0.932 / 0.933", "19.4"],
        ["B — Codex", "PDF / PNG", "0.000", "1.00", "1.00", "0.928 / 0.924", "45"],
        ["<b>AB</b> — Claude writes, source words place", "PDF / PNG", "0.000", "1.00", "1.00", "<b>921 (1.0) / 920 (0.999)</b>", "19.6 / 23.0"],
        ["AB — Codex", "PDF / PNG", "0.001", "1.00", "1.00", "0.999 / 0.999", "45 / 50"],
    ], numeric_from=2))
    add('<p class="defs"><b>Text NED</b>: how far the text is from the truth (0 = identical). <b>TEDS</b>: how close '
        'the tables\' structure is (1 = identical). <b>Header paths</b>: share of blocks filed under the right heading '
        'trail. <b>Words correctly placed</b>: words whose link points at their true box in the source.</p>'
        '<div class="callout"><b>The lesson, in plain words.</b> B\'s model wrote perfect text (NED 0.000), yet only '
        '93% of its words linked to the right place. Every misplaced word sat inside the model\'s own <i>correct</i> '
        'line box: the model found the line; our code then cut the line into words by character count and guessed the '
        'slices wrong. AB keeps the model\'s markdown and places each word with the source\'s own words (the PDF text '
        f'layer, else OCR) inside that line. Result: 921 of 921.{c(1, 5)}</div>'
        f'<p><b>HTML form</b> (pipeline A, conservative mode): 11 of 12 cases perfect (NED 0, TEDS 1, location 1.0, 0 '
        f'lost). Case 10, a torque-speed plot, scores NED 0.184 only because the markdown keeps the plot\'s 14 SVG axis '
        f'labels and the truth leaves them out — <a href="#decide">decision (c)</a>.{c(1)}</p>'
        '<h3>Real PDFs</h3><p class="small muted">No truth here, so coverage is measured against pdfplumber\'s own word '
        'list: the share of the source\'s words the markdown accounts for.</p>')
    add(table(["Pipeline", "Mean Well LRS-350 coverage", "TI TPS54331 (4 pp) coverage", "s / page"], [
        ["A", "1.0", "0.987", "~1 warm"],
        ["B Claude", "0.79", "0.91", "52"],
        ["AB Claude", "0.995", "1.0", "50"],
        ["AB Codex", "0.999", "1.0", "87"],
    ]))
    add(f'<ul class="small"><li>LRS-350 with A: 2 s for 4 pages. TPS54331 all 41 pages: coverage 0.989.</li>'
        f'<li>Mean Well RSP-500: 0.914, because its fonts carry no Unicode map — those words are flagged as lost, never '
        f'guessed.{c(12)}</li></ul>')
    add(fig(shot3, "Prototype viewer on the TI TPS54331: KPI strip 98.9% coverage, 99.6% traceable, 102 lost, 40 unlinked",
            f"TI TPS54331, all 41 pages, in the prototype viewer: the strip shows exactly what is lost (102 words) and "
            f"unlinked (40), and the ledger lights each one.{c(9)}"))
    add('<h3>Robotparts\' 46 real web pages: 0.510 → 0.987</h3>'
        '<ul>'
        f'<li><b>As shipped, coverage was 0.510.</b> Defuddle\'s reading mode is built for articles; it judged spec '
        f'tables to be clutter and dropped them — the CubeMars Specification tab, mdmbot\'s 额定扭矩 / 峰值扭矩 (rated / '
        f'peak torque), all of mdmbot.com.{c(4)}</li>'
        f'<li><b>Conservative mode drops only with a known reason</b> (navigation, footer, cookie banner). Coverage '
        f'0.987, traceability 0.995, lost words 16,017 → 466, and 0 links to the wrong text. The CubeMars catalogue menus '
        f'are dropped as navigation on all 17 pages; the Specification tab is kept on all 15 product pages.{c(4, 5)}</li>'
        f'<li>Defuddle\'s reading mode stays available as <code>--reader</code>.{c(3)}</li></ul>')
    add(fig(shot4, "Prototype viewer on an HTML comparison table: the archived page on the left, markdown on the right, the word Series lit in both",
            f"A web page in the same view: the archived page left, its markdown right; a word clicked on either side "
            f"lights on the other.{c(9)}"))
    add("</section>")

    # 5 · bugs
    add('<section id="bugs"><h2><span class="n">5</span>Bugs the KPIs caught</h2>'
        '<p>Each was found by a number going wrong, fixed in general (not for the one case), and pinned with a test.</p>'
        '<h3>1 · A table header shifted one column</h3>'
        f'<p>Mean Well LRS-350: the corner cell <i>MODEL</i> threw the header row off by one, so the markdown put '
        f'<code>350.4W</code> under <b>LRS-350-36</b> instead of <b>LRS-350-24</b>. A person or a model reading that '
        f'table would have been wrong. Fix: merged-cell tables are written on a rectangular grid, with empty filler '
        f'rather than a guessed merge (<code>src/mdify/tables.py</code>). A new <code>mdify check</code> rule, “tables are '
        f'rectangular”, found 7 of 23 cached tables ragged before, 0 after.{c(5, 12)}</p>'
        f'<figure>{shift_svg()}<figcaption>To scale, measured in the prototype viewer (x in px). Before: the value\'s centre '
        f'1206.3 sits outside the LRS-350-24 header (1097.8–1170.1). After: 1293.3 sits inside 1257.1–1329.5.{c(9, 12)}'
        f'</figcaption></figure>')
    add(fig(shot1, "Prototype viewer: the LRS-350 PDF spotlit on the 350.4W cell, the markdown table on the right with 350.4W highlighted under LRS-350-24",
            f"After the fix: <code>350.4W</code> sits under LRS-350-24 in the markdown (right), and clicking it "
            f"spotlights the same cell on the PDF (left).{c(9)}"))
    add('<h3>2 · Defuddle dropped half of every web page</h3>'
        f'<p>Coverage 0.510 on real pages; conservative mode brings it to 0.987 (above).{c(4)}</p>'
        '<h3>3 · The aligner stitched words together</h3>'
        f'<p>It could build “Mode” from the “M” of <i>Maximum</i> and the “ode” of <i>Model</i>. Now a markdown word only '
        f'matches whole source words.{c(5)}</p>'
        '<h3>4 · OCR read stacked header lines out of order</h3>'
        f'<p>JMC\'s selection list came out as “torque Rated (Nm)”. Cell text now follows visual reading order, and the '
        f'aligner follows the same order (417183a).{c(5)}</p>'
        '<h3>5 · The highlighter marked the first match</h3>'
        f'<p>The J60 page prints “30A” in both model columns; an HTML location used to land on whichever came first. Now '
        f'it resolves exactly, and an ambiguous one is reported, never guessed.{c(10)}</p>')
    if unresolved_alt:
        add(f'<div class="callout new"><b>New, found while building this page:</b> {unresolved_alt} of the 15 sampled '
            f'values ground on an image\'s <code>alt</code> text (“Wiring Pack: Power CAN cable ×1…”). Markdownify links '
            f'those words to a character range inside the <code>&lt;img&gt;</code> tag; the Source Highlighter refuses to '
            f'draw inside an attribute, as its contract says. The two contracts disagree, so the seam needs a rule: '
            f'ground alt text to the image element itself. Listed under <a href="#decide">left</a>.{c(13, 10)}</div>')
    add("</section>")

    # 6 · extractor
    add('<section id="extractor"><h2><span class="n">6</span>Datasheet Extractor</h2>'
        '<ul><li><b>Option 1</b>: the raw source <i>plus</i> its Markdownify bundle. The model cites cell and block ids; '
        'code, not the model, reads the row label and the model column from the span index.</li>'
        '<li><b>Option 2</b>: the raw source only. The model gives a quote and a box; the checker is robotparts\' own rule.</li></ul>'
        '<h3>Synthetic set (12 cases × 3 forms, 119 truth rows)</h3>')
    add(table(["Share of requested fields kept after the checker", "HTML", "PDF", "PNG"], [
        ["<b>Option 1</b> · Claude / Codex", "<b>0.982</b>", "<b>0.982</b>", "<b>0.939</b>"],
        ["Option 2 · Claude", "0.395", "0.351", "0"],
        ["Option 2 · Codex", "0.404", "0.395", "0"],
    ], caption="Precision is 1.0 for every kept value, both options. Location correct: Option 1 1.0, Option 2 0.91–0.95."))
    add('<div class="callout"><b>Claude read about 98% right under both options; Option 1 can prove 98%, Option 2 about a third.</b> The '
        'difference is not reading, it is what code can check. A quote cannot bind a number to its row label and its '
        'model column in a comparison table; a cell id can. Option 2 keeps nothing on PNG because its checker has no '
        f'OCR — robotparts\' rule for image-only sources.{c(6)}</div>'
        f'<p class="small muted">From the per-run table: under Option 2, Codex proposed values for 0.722 of the fields '
        f'(precision 1.0); Claude 0.991.{c(6)}</p>'
        '<h3>R0 — robotparts\' 20 parts, 217 quote-verified values</h3>')
    add(table(["Recall of robotparts' 217 values", "Claude", "Codex"], [
        ["Option 1", "0.977", "0.959"],
        ["Option 2", "0.982", "1.0"],
    ], caption="Caveat: robotparts' values were made by an Option-2-style pipeline, so Option 2 reproducing them is expected, not a win."))
    add(f'<ul><li><b>Option 1 found 41 new values</b>, 15 of them in multi-model comparison tables that robotparts\' '
        f'prompt skipped. 15 are sampled for you in <a href="#human-check">section 9</a>.{c(6, 7)}</li>'
        f'<li><b>Where Option 1 lost:</b> the J60 page prints each label and value as separate paragraphs; Option 1 '
        f'proposed nothing for J60-10 there (Option 2\'s quote “Peak Speed 15.49rad/s” passed).{c(6)}</li>'
        f'<li>7–25 s per document on the synthetic set, either option. 340 LLM calls for the whole evaluation.{c(6)}</li></ul>')
    add(fig(shot2, "Prototype values view: LRS-350-24 values kept with units and raw text, the PDF spotlit on the 88% efficiency cell",
            f"The Datasheet values view: 16 values kept, 2 dropped (with reasons). Click a row and the source spotlights "
            f"its cell; ranges read “0 – 14.6 A”, never JSON.{c(9)}"))
    add('<h3>JMC 60ASM200 — the first real job</h3>'
        f'<p>A supplier\'s selection list, as a WebP image (private). OCR read both rows exactly to RobotParts\' ground '
        f'truth.{c(8)}</p><ul>'
        '<li>After the reading-order fix, Option 1 kept 5 of 6, each with its cell id and box: rated power 200 W, rated '
        'torque 0.64 N·m, peak torque 1.3 N·m, rated speed 3000 rpm, max speed 3200 rpm.</li>'
        '<li>Nominal voltage 36 V was dropped, correctly: the header “Power supply voltage (V)” is not in the field '
        'list\'s labels (RobotParts\' call).</li>'
        '<li><b>Open gap:</b> OCR dropped the superscript minus in “10⁻⁴kg·cm²” and the word still scores 1.0, because '
        'OCR words are only compared with the OCR\'s own text. The sheet itself prints kg·cm² (likely a typo for kg·m²); '
        'raw is kept as printed.</li></ul>'
        f'<p><b>RobotParts\' response</b> to the vocabulary: nominal voltage gains “power supply voltage” / 电源电压; '
        f'rated and max <i>phase</i> current keys for actuators (RMS vs peak is ambiguous on the sheet); '
        f'<b>max speed 3200 is provisional</b> — JMC\'s “Max speed” may not be the canonical no-load '
        f'<code>max_output_speed</code>; rotor inertia stays manual with the raw string. RobotParts has taken '
        f'<code>values.json</code> as its contract and owns the SCHEMA2 amendment (stored copies; private account '
        f'material never in a public build). This lands on a robotparts lane, not main, and needs your merge.{c(12)} '
        f'<span class="muted">At build time <code>lane/jmc-sources</code> had no commits past main and a clean '
        f'worktree, so I could not see the change itself.</span></p></section>')

    # 7 · capture
    add('<section id="capture"><h2><span class="n">7</span>Capture</h2>'
        f'<p><code>mdify capture &lt;url&gt; --keep link,html,snapshot,pdf,archive</code>: one headless-browser visit '
        f'builds every level, prints each one\'s measured size, keeps the ones you pick and deletes the rest — your “not '
        f'either or”.{c(3)}</p>')
    add(table(["Page", "html", "snapshot JPG", "print-PDF", "full archive (WACZ)", "offline replay vs snapshot"], [
        ["CubeMars AK80-9", "167 KB", "1.5 MB", "5.2 MB", "6.1 MB (197 responses)", "0.09% pixels differ"],
        ["ZeroErr eRob80", "84 KB", "1.2 MB", "6.9 MB", "7.3 MB (117)", "0.08%"],
        ["moteus hardware", "45 KB", "164 KB", "95 KB", "285 KB (18)", "identical"],
    ]))
    add(f'<ul class="small"><li><b>Replay check:</b> the page reloaded from the WACZ alone with the network blocked, '
        f'compared with the snapshot. The only CubeMars difference is missing analytics beacons.{c(3)}</li>'
        f'<li><b>Shared files:</b> a site\'s pages share CSS, fonts and images, so 3 CubeMars pages are 9.7 MB in total '
        f'(the first 4.9 MB, then 2.1–2.7 MB each).{c(12)}</li>'
        f'<li><b>Default policy:</b> html always; snapshot + archive for pages a kept value cites; bulk scraping html only. '
        f'The CLI\'s own default is <code>--keep html,snapshot</code>; only the caller knows which pages a value cites, '
        f'so RobotParts applies the policy.{c(2, 3)}</li></ul></section>')

    # 8 · highlighter
    add('<section id="highlighter"><h2><span class="n">8</span>Source Highlighter</h2>'
        f'<p>Five styles (highlight, box, underline, spotlight, blackout) on PDF, image, HTML and text. 48 tests; marks '
        f'land within 0.03 px; the Python CLI and the browser module draw the same rectangles, and nudging them by 1.5 px '
        f'fails 17 tests.{c(10, 12)}</p><div class="gallery">')
    for g in shots_gallery:
        add(f'<figure><img src="{g["uri"]}" width="{g["w"]}" height="{g["h"]}" alt="{E(g["title"])}" loading="lazy">'
            f'<figcaption><b>{E(g["title"])}</b> — {g["caption"]}</figcaption></figure>')
    add(f'</div><p class="small muted">Real outputs from <code>/home/bam/source-highlighter/out/</code>, cropped to '
        f'the marks.{c(10)}</p></section>')

    # 9 · human check
    add('<section id="human-check"><h2><span class="n">9</span>Human check: 15 new values</h2>'
        f'<p>Values Option 1 found that robotparts did not have, sampled with a fixed seed. Each picture is the value\'s '
        f'own grounding, drawn by the Source Highlighter (spotlight) on the exact stored source, then cropped. Tick ✓ '
        f'right, ✗ wrong, ? unsure; add a note if you like. Your answers stay in this browser; <b>Copy my answers</b> '
        f'gives a short list to paste back.{c(7, 13)}</p>'
        '<p class="hint">On a phone each picture scrolls sideways; it opens centred on the mark.</p>'
        '<p class="small muted">Web pages render here without their live CSS (offline, scripts off), so they look plain; '
        'the text and table structure are the page\'s own.</p><div class="cards">')
    for card in cards:
        add(card_html(card))
    add('</div><div class="tally"><span id="tally-text">0 of 15 answered</span>'
        '<span><button type="button" id="copy-answers">Copy my answers</button> <span id="copy-status" class="small muted" role="status"></span></span></div>'
        '<textarea id="answers-out" readonly aria-label="Your answers as text"></textarea></section>')

    # 10 · decision surface
    add('<section id="decide"><h2><span class="n">10</span>Decisions</h2><h3>Done and proved</h3><ul>'
        f'<li>Markdownify A / B / AB measured on the ruler (921 words; AB 921/921 on PDF), on real PDFs, and on 46 real '
        f'web pages (0.987).{c(1, 4)}</li>'
        f'<li>Extractor: both options × both models × both sets, 340 LLM calls, precision 1.0 on every kept value.{c(6)}</li>'
        f'<li>Prototype viewer: {journey_ok}/{len(journey)} journey checks in the real host app — click ↔ highlight both ways within '
        f'0.03 px; every pane boundary drags and is remembered (source pane 595 → 375 px, kept after reload); ranges read '
        f'“0 – 14.6 A”.{c(9, 12)}</li>'
        f'<li>Source Highlighter: 48 tests, Python and JS agree.{c(10)}</li>'
        f'<li>Capture: three real pages, offline replay within 0.09% of the snapshot.{c(3)}</li>'
        f'<li>JMC first job: 5 of 6 values kept, each with cell id and box.{c(8)}</li>'
        f'<li>Value ids: every value carries <code>&lt;key&gt;@&lt;cite id&gt;</code>, stable within its file, so a '
        f'human edit can point at it (datasheet-extractor a100328). The <code>review.json</code> contract is written: '
        f'the AI record stays untouched and your edits, rejects and adds sit on top as attributed operations, Google '
        f'Docs suggestion-mode style (CONTRACT §7.1, ffb618f).{c(2, 5)}</li></ul>'
        '<h3>Left</h3><ul>'
        f'<li><b>Blocked on you:</b> RobotParts\' vocabulary change sits on a robotparts lane, not main.{c(12)}</li>'
        '<li>In progress: the viewer moves out of Clank into <code>mdify view</code>, Markdownify\'s own standalone '
        'page; Markdownify will not reference Clank. The Clank plugin stays only as today\'s recorded evidence.</li>'
        '<li>Next: spec tables inside iframes (eRob80) and as images in web pages → child bundles (decision b).</li>'
        + ('<li>Next (new today): alt-text groundings — ground an image\'s alt text to the image element, so the '
           'highlighter can draw it (2 of the 15 samples).</li>' if unresolved_alt else '')
        + '<li>Next: OCR confidence isn\'t carried into word scores (the lost “⁻” in 10⁻⁴ still scores as exact).</li>'
        '<li>Next: <code>harness/run.py</code> needs <code>--name</code>, so one pipeline\'s run can\'t overwrite another\'s.</li>'
        '<li>Next: the Source Highlighter fits PDF zoom to page 1 only (landscape pages run wide).</li>'
        '<li>Next: capture records through Playwright\'s HTTP client, which could trip bot checks on some sites.</li>'
        '<li>Housekeeping: an agent left an unused scratch folder it couldn\'t delete — run this yourself if you want it '
        'gone: <code>rm -rf /home/bam/.cache/clank-markdownify-explore</code></li></ul>'
        '<h3>Needs you</h3><div class="decide">')
    asks = [
        ("(a) Default pipeline for images?",
         "A stays the default (no AI, ~1 s a page). AB is opt-in with <code>mdify --pipeline AB</code>, and the extractor "
         "uses AB automatically for image sources, where A places 0.893 of words and AB 0.999.",
         "exactly that."),
        ("(b) iframes and images inside web pages?",
         "Each becomes a child bundle linked from the parent page (eRob80's spec table lives in an iframe).",
         "I build it next."),
        ("(c) Plot axis labels?",
         "Keep them as text in the markdown, and change the ruler's truth to include them (case 10's NED 0.184 is the "
         "truth leaving them out, not a pipeline error).",
         "keep them; the ruler change gets its own commit."),
        ("(d) Push the four repos to private GitHub?",
         "Yes, private — today they exist only on this disk, with no remote.",
         "they stay local until you say push."),
        ("(e) Publicly serving stored datasheets?",
         "Serve the stored copy with attribution, the original link beside it, and a takedown route; private account "
         "material never goes in a public build.",
         "as stated."),
        ("(f) The 15 new values",
         "Tick them in section 9 and paste the list back; ✗ ones get fixed at their cause, not one by one.",
         "they stay unchecked (<code>human_check: null</code>) and are not shown as verified."),
        "REVIEW_ROUND",
    ]
    for entry in asks:
        if entry == "REVIEW_ROUND":
            pages = sorted(REVIEW_ROUND.glob("*.html")) if REVIEW_ROUND.is_dir() else []
            page = next((x for x in pages if x.name == "index.html"), pages[0] if pages else None)
            where = (f'<a href="{page.as_uri()}">open the five proposals</a>' if page else
                     f'being built in <code>{E(str(REVIEW_ROUND))}/</code>; not ready when this page was built')
            add('<div class="ask"><div class="q">(g) Which review view for your Google-Docs-style edit mode?</div>'
                '<p class="rec"><b>Recommend:</b> pick one, or splice two, from five proposals — redline, margin, two '
                f'columns, layers, stream — all on the same <code>review.json</code> contract ({where}).</p>'
                '<p class="def"><b>If you say nothing:</b> the first proposal (redline) is the default; switching later '
                'changes only the view, never the data.</p></div>')
            continue
        q, rec, default = entry
        add(f'<div class="ask"><div class="q">{q}</div><p class="rec"><b>Recommend:</b> {rec}</p>'
            f'<p class="def"><b>If you say nothing:</b> {default}</p></div>')
    add('</div><h3>Deliberately not done</h3><ul>'
        '<li>No audit — those run only when you ask. Worth a <code>/llm-judge</code>: the extractor\'s checker, because '
        'it decides what RobotParts shows as verified.</li>'
        '<li>Nothing merged into robotparts.</li><li>Nothing pushed.</li>'
        '<li>Scans are out of scope (digital sources only).</li>'
        '<li>Plots and diagrams are not converted; the ladder shows them as 0/0, as you said is fine for now.</li></ul></section>')

    # 11 · run
    add('<section id="run"><h2><span class="n">11</span>How to run each thing</h2>')
    add(command("/usr/bin/node /home/bam/clank-markdownify/scripts/demo.mjs",
                "<b>Prototype viewer, as recorded</b> — today's Clank plugin on real datasheets; open a PDF → "
                "<i>Open with… → Markdownify</i>, or <code>lrs-350-24.values.json</code> → Datasheet values (serves "
                ":5480, host :5481). Being replaced by <code>mdify view</code>"))
    add(command("uv run --directory /home/bam/markdownify mdify /home/bam/markdownify/harness/out/04-multi-model/pdf/source.pdf",
                "<b>Markdownify, pipeline A</b> (no AI) — bundle lands in <code>~/.cache/markdownify/bundles/&lt;sha256&gt;/</code>"))
    add(command("uv run --directory /home/bam/markdownify mdify /home/bam/markdownify/harness/out/04-multi-model/png/source.png --pipeline AB",
                "<b>Markdownify, pipeline AB</b> — the model writes, the source's words place (20–50 s a page)"))
    add(command("uv run --directory /home/bam/markdownify mdify check /home/bam/markdownify/harness/out/runs/pipeline-a/04-multi-model/pdf/bundle",
                "<b>mdify check</b> — re-derives a bundle's invariants (tables rectangular, every word accounted for)"))
    add(command("uv run --directory /home/bam/markdownify mdify capture https://www.cubemars.com/product/ak80-8-kv60-robotic-actuator.html --dry-sizes",
                "<b>Capture sizes</b> — one visit, measures every level, keeps nothing"))
    add(command("uv run --directory /home/bam/datasheet-extractor extract /home/bam/source-highlighter/tests/fixtures/j60-joint.pdf --fields /home/bam/datasheet-extractor/fields/actuator.json --option 1 --part-model J60-10 --out /home/bam/.cache/datasheet-extractor/try/j60-option1",
                "<b>Datasheet Extractor, Option 1</b> — one LLM call, writes <code>values.json</code>"))
    add(command("uv run --directory /home/bam/source-highlighter source-highlighter /home/bam/source-highlighter/tests/fixtures/j60-joint.pdf --locations /home/bam/source-highlighter/tests/fixtures/j60-joint.locations.json --style spotlight --out /home/bam/source-highlighter/out/j60-spotlight.png",
                "<b>Source Highlighter</b> — the J60 spotlight in the gallery"))
    add(command("uv run --directory /home/bam/markdownify python /home/bam/markdownify/harness/run.py --pipeline 'uv run mdify {source} --out {bundle}'",
                "<b>The ruler</b> — re-runs pipeline A over all 12 cases (overwrites its own previous result)"))
    add(command("uv run --directory /home/bam/markdownify python /home/bam/markdownify/docs/build_report.py",
                "<b>Rebuild this page</b> — reuses its renders; add <code>--fresh</code> to redraw them"))
    add("</section>")

    # sources
    add('<section id="sources"><h2>Source Index</h2><ol class="sources">')
    for number, (label, where) in enumerate(SOURCES, 1):
        link = ""
        if where.startswith("/home/bam/zach_brain/"):
            vault_path = where.removeprefix("/home/bam/zach_brain/").removesuffix(".md")
            link = (f' · <a href="obsidian://open?vault=zach_brain&amp;file={urllib.parse.quote(vault_path)}">open in '
                    f'Obsidian</a>')
        add(f'<li id="src-{number}">{label} — <code>{E(where)}</code>{link}</li>')
    add('</ol><p class="small muted">Built by <code>/home/bam/markdownify/docs/build_report.py</code>. Every image and '
        'the video are inlined; the page needs no network.</p></section>')

    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"color-scheme\" content=\"light dark\">"
            "<title>Datasheet Ingestion Review</title>"
            f"<style>{CSS}</style></head><body><main>" + "".join(parts) + f"</main><script>{JS}</script></body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".html.tmp")
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(OUT)  # WHY: never leave a half-written page where the open fence points
    return OUT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fresh", action="store_true", help="redraw the hero and every Source Highlighter render")
    arguments = parser.parse_args()
    path = build(arguments.fresh)
    print(f"{path}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
