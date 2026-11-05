#!/usr/bin/env python3
"""Build Zach's review page for the 2026-11-05 round: one self-contained HTML file.

    uv run --directory /home/bam/markdownify python docs/build_report_2026_11_05.py [--fresh]

Covers the Sources app (datasheet-extractor), the linked-source model (References in the CRM and ObjectDB), the
standalone Source Highlighter viewer (Peek → Lens) and the capture trial. Writes
reports/media/linked-sources-2026-11-05.html. WHY a new page and not the 2026-10-29 one: two more hero videos would
push that page past the desktop preview's ~1.9 MB. WHY reports/media/ (gitignored): the page inlines two videos and six
screenshots as data URIs, so it opens offline from any folder.

The recorded media is read from where each repo's own journey wrote it (datasheet-extractor and source-highlighter
reports/media/); re-encoded videos go to reports/media/linked-sources-2026-11-05.build/ and are reused on the next
run; --fresh re-encodes them. Mechanics (data URIs, ffmpeg re-encode, JPEG stills, light/dark, phone width) follow
docs/build_report.py.
"""

from __future__ import annotations

import argparse
import base64
import glob
import html
import io
import pathlib
import shutil
import subprocess
import sys
import urllib.parse

from PIL import Image

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "media" / "linked-sources-2026-11-05.html"
WORK = REPO / "reports" / "media" / "linked-sources-2026-11-05.build"

SOURCES_APP_MEDIA = pathlib.Path("/home/bam/datasheet-extractor/reports/media/sources-app")
VIEWER_MEDIA = pathlib.Path("/home/bam/source-highlighter/reports/media/viewer")
VIEWER_GALLERY = pathlib.Path(
    "/home/bam/source-highlighter/reports/media/viewer-proposals-2026-11-05/viewer-proposals-2026-11-05.html")
REVIEW_GALLERY = pathlib.Path(
    "/home/bam/datasheet-extractor/reports/media/review-view-proposals-2026-10-29/review-view-proposals-2026-10-29.html")
OCT29_REPORT = REPO / "reports" / "media" / "datasheet-ingestion-2026-10-29.html"
VAULT_NOTE = ("Projects/Markdownify/Linked Sources — Off-the-Shelf Citation Viewers and NotebookLM, "
              "Prior Art (2026-11-05).md")
LRS350_BUNDLE = pathlib.Path(
    "/home/bam/.cache/markdownify/bundles/ac3e7b8d27dcd72277b6bdbb208586a2343a635edf1ff578fffd131a31713f6f")
VIEWER_REFS = pathlib.Path("/home/bam/source-highlighter/reports/media/viewer-proposals-2026-11-05/src/fixtures/refs.json")

# WHY these sizes: Zach's desktop preview stops at ~1.9 MB for a media page; CRF 32 at 1280 px keeps the review
# table's 13 px text legible (checked frame by frame), JPEG q80 keeps UI text crisp.
SCREENSHOT_WIDTH = 1200
JPEG_QUALITY = 80
VIDEO_CRF = 32


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


def video(source: pathlib.Path, name: str, fresh: bool) -> bytes:
    """A real recording, re-encoded smaller (H.264, 1280 px, CRF 32): ~450 KB → ~190 KB, text still legible."""
    target = WORK / f"{name}-1280-crf{VIDEO_CRF}.mp4"
    if fresh or not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-i", str(source), "-an",
                        "-vf", "scale=1280:-2:flags=lanczos", "-c:v", "libx264", "-preset", "veryslow",
                        "-tune", "stillimage", "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-r", "25", str(target)], check=True)
    return target.read_bytes()


def still(path: pathlib.Path, crop: tuple[int, int, int, int] | None = None,
          width: int = SCREENSHOT_WIDTH) -> tuple[str, int, int]:
    """A screenshot as a JPEG data URI, optionally cropped to (left, top, right, bottom) px first."""
    image = Image.open(path)
    if crop:
        image = image.crop(crop)
    data = jpeg_bytes(image, width)
    shown = Image.open(io.BytesIO(data))
    return data_uri(data, "image/jpeg"), shown.width, shown.height


# ---------------------------------------------------------------- page text helpers

E = html.escape

SOURCES = [
    ("The orchestrator's verified fact sheet for this round (every number on this page that has no repo file of its "
     "own)", "session scratchpad REPORT-FACTS-1105.md"),
    ("Datasheet Extractor — Sources app contract v0: state on disk, queue stages, HTTP API, effective record, "
     "review tab (the splice), 9c627c3", "/home/bam/datasheet-extractor/CONTRACT.md"),
    ("Datasheet Extractor commits c403aad … f4c11ae, 46d75a2 (backend, UI, fixes); 52 tests",
     "git -C /home/bam/datasheet-extractor log"),
    ("Sources app journey evidence from the real server: controls checked, spotlight error, drag widths, ops on disk",
     "/home/bam/datasheet-extractor/reports/media/sources-app/journey-results-real.json"),
    ("Sources app hero recording facts: queue 81.9 s real time, sped up 20.2×, three review ops by Zach Yamaoka",
     "/home/bam/datasheet-extractor/reports/media/sources-app/media-results-real.json"),
    ("Markdownify CONTRACT §7.1 map op (d37fb09); capture levels §2.1 incl. `single` (50ccc8b)",
     "/home/bam/markdownify/CONTRACT.md"),
    ("Source Highlighter CONTRACT §5 Reference and §6 live first, stored copy as fallback (92ce94a, 72d9c62, "
     "4cfcedb, 91514e9, 9b513b1)", "/home/bam/source-highlighter/CONTRACT.md"),
    ("Source Highlighter commits: 0c68a4d check, a724a7c view (Lens + Peek), 155b1c7 Lens Details, 9a405fc file:// "
     "decoding; 81 tests", "git -C /home/bam/source-highlighter log"),
    ("Viewer evidence: the real `check` run (CubeMars and Mean Well both live), recorded 2026-11-05 20:24 UTC",
     "/home/bam/source-highlighter/reports/media/viewer/statuses.json"),
    ("CRM References: bam-data 71f814c, “References in the Source Highlighter's format” (147 References, ref pinned on "
     "each Evidence that cites exactly one)", "git -C /home/bam/bam-data show 71f814c"),
    ("ObjectDB reference sidecars, one per mix, link-only today", "/home/bam/bam-waste-objects/refs/"),
    ("Prior-art research note: NotebookLM, citation viewers, libraries, live-versus-stored practice",
     "/home/bam/zach_brain/" + VAULT_NOTE),
    ("The viewer round: five proposals, gates, AI ranking", str(VIEWER_GALLERY)),
    ("Capture trial: ours vs SingleFile vs Scoop, ReplayWeb.page replay, politeness, code Scoop would replace",
     "/home/bam/markdownify/docs/research/2026-11-05-capture-trial.md"),
    ("The 2026-10-29 review report (Markdownify, extractor, highlighter; the 15 values to tick in §9)",
     str(OCT29_REPORT)),
    ("The 2026-10-29 review-view round: five proposals for the review tab", str(REVIEW_GALLERY)),
]


def c(*numbers: int) -> str:
    """An inline citation: bare numbers, comma-joined, linking to the Source Index."""
    links = ",".join(f'<a href="#src-{n}">{n}</a>' for n in numbers)
    return f'<sup class="cite">{links}</sup>'


def table(head: list[str], rows: list[list[str]], numeric: frozenset[int] = frozenset(), caption: str = "",
          row_classes: list[str] | None = None, min_width: int = 0) -> str:
    # WHY min_width: a table with a prose column scrolls sideways on a phone instead of squeezing that column to a sliver.
    out = [f'<div class="tw"><table{f" style=\"min-width:{min_width}px\"" if min_width else ""}>']
    if caption:
        out.append(f"<caption>{caption}</caption>")
    out.append("<thead><tr>" + "".join(f"<th{' class=\"num\"' if i in numeric else ''}>{h}</th>"
                                        for i, h in enumerate(head)) + "</tr></thead><tbody>")
    for index, row in enumerate(rows):
        klass = f' class="{row_classes[index]}"' if row_classes and row_classes[index] else ""
        out.append(f"<tr{klass}>" + "".join(f"<td{' class=\"num\"' if i in numeric else ''}>{cell}</td>"
                                            for i, cell in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def command(line: str, label: str) -> str:
    return (f'<div class="cmd"><div class="cmd-label">{label}</div><div class="cmd-row"><pre><code>{E(line)}</code></pre>'
            f'<button class="copy" type="button" data-copy="{E(line)}">Copy</button></div></div>')


def file_link(path: pathlib.Path, text: str, anchor: str = "") -> str:
    return f'<a href="{E(path.as_uri() + anchor)}">{text}</a>'


def vault_link(text: str) -> str:
    uri = ("obsidian://advanced-uri?vault=zach_brain&filepath="
           + urllib.parse.quote(VAULT_NOTE, safe=""))
    return f'<a href="{E(uri)}">{text}</a>'


def undo(repo: str, *shas: str) -> str:
    return f'<code class="undo">git -C /home/bam/{repo} revert {" ".join(shas)}</code>'


# ---------------------------------------------------------------- page

CSS = r"""
:root{--bg:#fbfbfa;--surface:#fff;--surface-2:#f3f3f0;--ink:#1c1c1e;--muted:#5f5f67;--line:#e3e3df;--accent:#2f5fd0;
--accent-soft:#eaf0fd;--good:#1d7a4b;--good-soft:#e2f3e9;--bad:#b3261e;--bad-soft:#fbe8e6;--warn:#8a5d00;--warn-soft:#fbf0d9;
--code:#f3f3f0;--bar:#c9d6f5;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#131315;--surface:#1b1b1e;--surface-2:#232327;
--ink:#ececef;--muted:#a3a3ab;--line:#34343a;--accent:#8fb0ff;--accent-soft:#1d2741;--good:#63d39c;--good-soft:#15331f;
--bad:#ff8f86;--bad-soft:#3c1b19;--warn:#f0c46a;--warn-soft:#382d13;--code:#1f1f23;--bar:#33456f;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#131315;--surface:#1b1b1e;--surface-2:#232327;--ink:#ececef;--muted:#a3a3ab;--line:#34343a;
--accent:#8fb0ff;--accent-soft:#1d2741;--good:#63d39c;--good-soft:#15331f;--bad:#ff8f86;--bad-soft:#3c1b19;--warn:#f0c46a;
--warn-soft:#382d13;--code:#1f1f23;--bar:#33456f;color-scheme:dark}
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
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;align-items:start}
.shots figure{margin:0}
.shots.wide{grid-template-columns:minmax(0,1fr)}
.tw{overflow-x:auto;margin:10px 0;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14.5px}
caption{text-align:left;padding:10px 12px 0;color:var(--muted);font-size:13.5px}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{font-weight:600;background:var(--surface-2);white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.win td{background:var(--good-soft)}
tr.no td{background:var(--bad-soft)}
.defs{font-size:14px;color:var(--muted)}
.defs b{color:var(--ink);font-weight:600}
.callout{border:1px solid var(--line);background:var(--surface);border-radius:10px;padding:12px 14px;margin:12px 0}
.callout.warn{border-color:var(--warn);background:var(--warn-soft)}
.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:13px;font-weight:600;white-space:nowrap;border:1px solid var(--line);background:var(--surface-2)}
.pill.live{color:var(--good);border-color:var(--good);background:var(--good-soft)}
.pill.deviated{color:var(--warn);border-color:var(--warn);background:var(--warn-soft)}
.pill.dead{color:var(--bad);border-color:var(--bad);background:var(--bad-soft)}
.chain{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0;font-size:14px}
.chain span.step{border:1px solid var(--line);background:var(--surface);border-radius:6px;padding:2px 9px}
.chain span.step.first{border-color:var(--accent);color:var(--accent);font-weight:600}
.chain span.arrow{color:var(--muted)}
.decided{display:grid;gap:10px;margin-top:10px}
.decided .d{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:10px 14px}
.decided .d .what{font-weight:650}
.decided .d p{margin:4px 0 0;font-size:14.5px}
.decided .d .u{font-size:13.5px;color:var(--muted);margin-top:6px}
code.undo{font-size:12.5px}
.rank{display:grid;gap:6px;margin:10px 0}
.rank .r{display:grid;grid-template-columns:150px 1fr 40px;gap:10px;align-items:center;font-size:14.5px}
.rank .r .name b{font-weight:650}
.rank .r .track{background:var(--surface-2);border-radius:6px;height:18px;overflow:hidden;border:1px solid var(--line)}
.rank .r .fill{height:100%;background:var(--bar)}
.rank .r.built .fill{background:var(--accent)}
.rank .r .score{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.ask{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:12px 14px;margin:10px 0}
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
@media (max-width:560px){h1{font-size:22px}h2{font-size:19px}.verdict p.v{font-size:16px}th,td{padding:7px 9px}
header.top{flex-direction:column}.rank .r{grid-template-columns:118px 1fr 34px;gap:8px;font-size:13.5px}}
"""

JS = r"""
(function(){
  var THEME_KEY='linked-sources-2026-11-05/theme';
  function get(k){try{return window.localStorage.getItem(k)}catch(e){return null}}
  function set(k,v){try{window.localStorage.setItem(k,v);return true}catch(e){return false}}
  var root=document.documentElement, themeBtn=document.getElementById('theme');
  function applyTheme(t){if(t==='light'||t==='dark'){root.setAttribute('data-theme',t)}else{root.removeAttribute('data-theme')}
    if(themeBtn){themeBtn.textContent='Theme: '+(t||'auto')}}
  var theme=get(THEME_KEY); applyTheme(theme);
  if(themeBtn){themeBtn.addEventListener('click',function(){theme=theme==='light'?'dark':(theme==='dark'?null:'light');
    applyTheme(theme); if(theme){set(THEME_KEY,theme)}else{try{window.localStorage.removeItem(THEME_KEY)}catch(e){}}})}
  // WHY: some browsers start a muted autoplay video only when muted is set as a property, not just the attribute.
  [].forEach.call(document.querySelectorAll('video[autoplay]'),function(v){v.muted=true;var p=v.play&&v.play();if(p&&p.catch){p.catch(function(){})}});
  [].forEach.call(document.querySelectorAll('button.copy'),function(b){b.addEventListener('click',function(){
    var text=b.getAttribute('data-copy');
    function mark(ok){b.textContent=ok?'Copied':'Select it';setTimeout(function(){b.textContent='Copy'},1600)}
    try{navigator.clipboard.writeText(text).then(function(){mark(true)},function(){mark(false)})}catch(e){mark(false)}
  })});
})();
"""


def build(fresh: bool) -> pathlib.Path:
    WORK.mkdir(parents=True, exist_ok=True)
    hero_sources = video(SOURCES_APP_MEDIA / "hero.mp4", "sources-app", fresh)
    hero_viewer = video(VIEWER_MEDIA / "hero-peek-to-lens.mp4", "peek-to-lens", fresh)
    # Crops cut away empty page around the UI; the review tab and Lens stay whole (every pane matters there).
    add_sources = still(SOURCES_APP_MEDIA / "1-add-sources.png", (262, 56, 1018, 800), width=760)
    queue = still(SOURCES_APP_MEDIA / "2-queue.png", (150, 58, 1130, 222), width=980)
    review = still(SOURCES_APP_MEDIA / "3-review.png")
    peek_hover = still(VIEWER_MEDIA / "1-peek-hover-crop.png", (250, 30, 1030, 545), width=780)
    peek_pinned = still(VIEWER_MEDIA / "2-peek-pinned.png", (740, 40, 1262, 662), width=522)
    lens = still(VIEWER_MEDIA / "3-lens-details-status.png")

    def fig(shot: tuple[str, int, int], alt: str, caption: str) -> str:
        uri, w, h = shot
        return (f'<figure><img src="{uri}" width="{w}" height="{h}" alt="{E(alt)}" loading="lazy">'
                f"<figcaption>{caption}</figcaption></figure>")

    def hero(data: bytes, label: str, caption: str) -> str:
        return ('<figure><video autoplay muted loop playsinline controls preload="auto" width="1280" height="800" '
                f'aria-label="{E(label)}"><source src="{data_uri(data, "video/mp4")}" type="video/mp4">'
                'Your viewer cannot play this video; the stills below show the same flow.</video>'
                f'<figcaption>{caption}</figcaption></figure>')

    parts: list[str] = []
    add = parts.append

    # ------------------------------------------------------------ header + 1 · verdict
    add('<header class="top"><div><h1>Linked Sources</h1>'
        '<p class="sub">Review packet · 2026-11-05 · the Sources app, References in the CRM and ObjectDB, the '
        'Peek → Lens viewer, the capture trial</p></div>'
        '<button class="theme" id="theme" type="button">Theme: auto</button></header>')
    add('<nav class="toc" aria-label="Sections">'
        + "".join(f'<a href="#{a}">{t}</a>' for a, t in [
            ("verdict", "Verdict"), ("hero-sources", "Sources app"), ("hero-viewer", "Peek → Lens"),
            ("decided", "Decided for you"), ("sources-app", "Sources results"), ("linked", "Linked sources"),
            ("prior-art", "Prior art"), ("viewer", "Viewer round"), ("capture", "Capture trial"),
            ("decide", "Decisions"), ("run", "Run it"), ("index", "Source Index")])
        + "</nav>")
    add('<section id="verdict"><div class="verdict">'
        '<p class="v">Datasheets now go in through one page (files and links → a queue → one review tab per source), '
        'and anything that cites a source (an extracted value, a CRM note, an ObjectDB mix) can open it on the exact '
        'spot, saying whether the live page still matches.</p>'
        '<p class="w">On <code>main</code> in markdownify, datasheet-extractor and source-highlighter and on '
        '<code>master</code> in ObjectDB (bam-waste-objects): four local repos with no remote, nothing pushed. The '
        'CRM\'s References are bam-data 71f814c, its own agent\'s commit, already on its private GitHub.'
        f'{c(3, 8, 10, 11)}</p></div>'
        '<ul class="highlights">'
        f'<li><b>The Sources app works on real datasheets.</b> Mean Well LRS-350 kept 32 values in the new “every '
        f'spec” mode, CubeMars AK80-9 kept 16 of 16, JMC\'s WebP list 5 of 6. The real-server journey passes 68 of '
        f'68 checks.{c(1, 4)}</li>'
        f'<li><b>One Reference format for everything that cites.</b> The CRM now holds 147 References pinned on 215 '
        f'evidence entries; ObjectDB has reference files for 13 mixes. Each one is checked against the live source '
        f'and labelled <span class="pill live">live</span> <span class="pill deviated">deviated</span> '
        f'<span class="pill dead">dead</span> or <span class="pill">link-only</span>.{c(1, 7, 10)}</li>'
        f'<li><b>Peek → Lens is built.</b> Hover a citation anywhere and a crop of the source pops up; click to pin '
        f'it; open it full page with its status and what was kept. Marks land within 0.026 px.{c(1, 8)}</li>'
        f'<li><b>Prior art: nothing off the shelf does the whole job.</b> NotebookLM highlights its own extracted '
        f'text, never the original, and never re-checks a web page. So the highlighter stays, and stock parts do '
        f'what they can: SingleFile and ReplayWeb.page are in; Scoop lost its trial.{c(12, 14)}</li>'
        '<li><b>Your move:</b> nothing blocks. The 15 values from Oct 29 are still yours to tick, and the repos stay '
        'local until you say push (<a href="#decide">section 10</a>).</li></ul></section>')

    # ------------------------------------------------------------ 2 · hero, Sources app
    add('<section id="hero-sources"><h2><span class="n">2</span>The Sources app, working</h2>')
    add(hero(hero_sources, "The Sources app: add a PDF and a link, watch the queue, review values beside the source",
             '14 s, muted, recorded from the real server (<code>extract serve</code>). Add the LRS-350 PDF and the '
             'CubeMars link with product Mean Well LRS-350-24 and category <i>psu</i> → the queue runs capture, '
             'convert, extract (1 min 20 s of real time, sped up 20× and labelled) → the review tab: map the printed '
             'model LRS-350-24 to your product, change rated power 350.4 → 350, strike the ripple value. Each act '
             f'lands in the version log under your initials.{c(4, 5)}'))
    add('<div class="callout warn small"><b>One thing the recording still shows:</b> the “As printed” column reads '
        '<code>0 &amp;#126; 14.6A</code>, an HTML entity left in the stored text. It was fixed three minutes after '
        'this was recorded (46d75a2): the column now stores what the page prints, <code>0 ~ 14.6A</code>, and keeps '
        f'the model\'s own copy separately. The stills below are from the same run.{c(1, 3)}</div>')
    add('<div class="shots wide">'
        + fig(add_sources, "Add sources: a dropped PDF, one link, the product Mean Well LRS-350-24, category psu, and the capture levels for web pages with their measured sizes",
              f"<b>Add sources.</b> Any number of files and links in one batch; products and a category for the "
              f"batch; for links, what to keep, each level with its size measured on a real page.{c(2)}")
        + fig(queue, "The queue: the batch of two, both extracted, 2 of 2 ready to review",
              f"<b>Queue.</b> One row per source with its stage (added → captured → converted → extracted) and its "
              f"own category; <i>Review</i> opens the tabs.{c(2)}")
        + fig(review, "The review tab: the LRS-350 PDF spotlit on the left; on the right the models this sheet covers, the values table with provenance, and the version log",
              f"<b>Review tab</b> (the splice, see <a href=\"#decided\">section 4</a>). Left: the source, the clicked "
              f"value spotlit. Right, top: <i>this datasheet covers 8 models → map to your parts</i>. Then every value "
              f"with who touched it (faint “AI · 29 Oct” until you act), and the version log at the bottom.{c(2, 4)}")
        + '</div></section>')

    # ------------------------------------------------------------ 3 · hero, Peek → Lens
    add('<section id="hero-viewer"><h2><span class="n">3</span>Peek → Lens, working</h2>')
    add(hero(hero_viewer, "Peek: hover a citation, a crop of the source pops up; click pins it; Open full shows it in Lens",
             '13.9 s, muted, a real recording of <code>source-highlighter view</code>. Three demo host cards stand '
             'in for a CRM note, an ObjectDB record and a plain page. Hover <code>9 N·m¹</code> → a crop of the '
             'CubeMars page with “Rated torque (Nm) 9” marked and its status. Hover <code>350.4 W²</code>, click to '
             'pin a panel you can move and resize → <i>Open full</i> → Lens: the LRS-350 page with the cell '
             f'spotlit, then <i>Details</i>.{c(8, 9)}'))
    add('<p class="defs"><b>Peek</b>: the small pop-up for citations inside any page (a script tag adds it). '
        '<b>Lens</b>: one reference, full page. <b>Details</b>: Lens\'s side panel, the status, both links, which '
        'copies were kept and where in the source.</p>')
    add('<div class="shots">'
        + fig(peek_hover, "Peek on hover: a crop of the CubeMars specification table with Rated torque (Nm) 9 highlighted, the status live · unchanged since 2026-11-05, live and stored links",
              f"<b>Hover.</b> The crop around the mark, the status pill, and both links: the live page and the "
              f"dated stored copy.{c(8, 9)}")
        + fig(peek_pinned, "Peek pinned: a floating panel showing the LRS-350 PDF with 350.4W highlighted under LRS-350-24",
              f"<b>Pinned.</b> The whole source in a floating panel; its position and size are remembered.{c(1, 8)}")
        + '</div><div class="shots wide">'
        + fig(lens, "Lens with Details open: the LRS-350 page spotlit on the 350.4W cell; the panel shows live · unchanged since 2026-10-29, byte-identical, links, where, what was kept, locations, mark style",
              f"<b>Lens + Details.</b> “byte-identical to the cited copy (same sha256)”; <i>What was kept</i> lists "
              f"every level (here only the PDF, 658 KB) and which one is shown.{c(8, 9)}")
        + '</div></section>')

    # ------------------------------------------------------------ 4 · decided for you
    decided = [
        ("Review tab = the splice",
         "V5 Stream as the base, V4's “covers N models → map to your parts” step on top, V3's per-product columns as a "
         "toggle. No pick came in a week, so the recommendation became the default. Switching later changes only the "
         f"view; your edits live in <code>review.json</code> either way.{c(2, 16)}",
         undo("datasheet-extractor", "264b548") + " (the whole review UI; the later UI commits build on it, so "
         "picking another proposal is the lighter undo)"),
        ("Sources app contract",
         "State in <code>~/.local/share/datasheet-extractor</code>; queue stages; the HTTP API; the effective record "
         f"(the AI's values with your ops applied in order); publishing one datasheet per product.{c(2)}",
         undo("datasheet-extractor", "9c627c3")),
        ("The <code>map</code> op",
         "A printed model (LRS-350-24) is one of your products: its values follow it unless an assign or unassign "
         f"says otherwise.{c(6)}",
         undo("markdownify", "d37fb09")),
        ("Reference format and live-first fallback",
         "Status words from Wikipedia's url-status practice; “unchanged” means an exact match; the live side is a "
         "link your browser highlights (Text Fragment or <code>#page=N</code>), never a re-render; dead = three "
         f"failures a day apart; a robots.txt refusal reads “not checked”.{c(7, 12)}",
         undo("source-highlighter", "9b513b1", "91514e9", "4cfcedb", "72d9c62", "92ce94a")),
        ("Viewer = Peek inline → Lens full page",
         f"With V5's fallback chain inside Lens Details. The AI ranking behind it is fragile (section 8).{c(13, 8)}",
         undo("source-highlighter", "155b1c7", "a724a7c")),
        ("SingleFile adopted as a display-only <code>single</code> level",
         f"A one-file offline copy to look at; values keep citing <code>source.html</code>.{c(14)}",
         undo("markdownify", "b96e144")),
        ("ReplayWeb.page behind a “Full archive” button in <code>mdify view</code>",
         f"Replays the stored archive with 0 requests off the machine.{c(14)}",
         undo("markdownify", "c50c089")),
        ("Scoop not adopted",
         f"It lost the trial on completeness and replay here, and skips robots.txt (section 9).{c(14)}",
         "Nothing to revert: no Scoop code landed. Re-run the trial when Scoop gains a page hook or robots.txt "
         "support (50ccc8b records how)."),
    ]
    add('<section id="decided"><h2><span class="n">4</span>Decided for you</h2>'
        '<p>My recommendation is the default when you haven\'t picked. Each call is one revert away.</p><div class="decided">')
    for what, why, how in decided:
        add(f'<div class="d"><div class="what">{what}</div><p>{why}</p><div class="u">Undo: {how}</div></div>')
    add('</div></section>')

    # ------------------------------------------------------------ 5 · sources app results
    add('<section id="sources-app"><h2><span class="n">5</span>Sources app results</h2>'
        '<h3>Real run</h3>')
    add(table(["Source", "Fields asked", "Kept", "Dropped", "What it shows"], [
        ["JMC selection list (WebP image)", "actuator", "5", "1",
         "Row 60ASM200-1024C/2500C matched to the product after two fixes (below)."],
        ["Mean Well LRS-350 (PDF)", "<i>psu</i>, open “every spec” mode", "32", "20",
         "17 of the 20 are ambiguous: one value printed under several conditions. Kept e.g. DC voltage 24 V, rated "
         "current 14.6 A, rated power 350.4 W, ripple 150 mVp-p."],
        ["CubeMars AK80-9 (link)", "actuator, 18 fields", "16", "0", "A web page captured as html + snapshot."],
    ], numeric=frozenset({2, 3}), min_width=640, caption="Open mode (new): for a category with no field list, the extractor asks for every "
                               "specification as printed, under the same structural checks."))
    add(f'<ul><li><b>Publishing:</b> 15 values written for the mapped product; the rejected one left out; the edited '
        f'one keeps the AI\'s original beside your value.{c(1, 2)}</li>'
        f'<li><b>Also in:</b> a category per source; a link-only source stops at “captured” with a note instead of '
        f'failing; the author is your git name (shown as ZY); field labels as the sheet writes them; an '
        f'“extracting…” flag while a job runs.{c(1, 3)}</li>'
        f'<li><b>Proof:</b> real-server journey 68 of 68 checks (stub server 67 of 67); spotlight within 0.014 px; '
        f'every pane drags and is remembered (source pane 690 → 550 px, still 550 after reload); 52 tests.{c(1, 4)}</li>'
        f'<li><b>Nothing regressed in the extractor:</b> after every checker change, a rejudge (no LLM calls) gives '
        f'scores identical to the committed results: Option 1 keeps 334 synthetic values per model; R0 recall '
        f'0.977 / 0.959.{c(1)}</li></ul>')
    add('<h3>Bugs found by the real run, and fixed</h3><ul>'
        f'<li><b>JMC\'s product never matched its row.</b> The product provider read only <code>part.json</code> '
        f'(20 parts); it now reads <code>part.py</code> too (68). And the printed name drops the option segment: '
        f'product 60ASM200-<b>5</b>-1024C now finds row 60ASM200-1024C/2500C, while AK80-9 still never reads as '
        f'AK80-64 (57492cd).{c(3)}</li>'
        f'<li><b>Merged-cell tables arrived with HTML entities</b> (<code>&amp;#126;</code> for “~”), so checks '
        f'compared the wrong text. One reader now decodes entities for every checker (b0ca0f4, 86a2b32).{c(3)}</li>'
        f'<li><b>“As printed” showed the entity</b>, as the hero still does. It now stores the decoded text the '
        f'source prints, <code>0 ~ 14.6A</code> (46d75a2).{c(1, 3)}</li>'
        f'<li><b>A crashed job left “running” forever.</b> A restart now clears it and records the failure '
        f'(fa0b6a0).{c(3)}</li></ul></section>')

    # ------------------------------------------------------------ 6 · linked sources
    add('<section id="linked"><h2><span class="n">6</span>Linked sources</h2>'
        '<h3>The Reference format, in plain words</h3>'
        f'<p>A <b>Reference</b> is one cited thing: <b>which source</b> (a web address, a local file or a vault '
        f'note), <b>what was kept</b> of it (nothing, html, snapshot, pdf, full archive, or any mix), <b>where in '
        f'it</b> (Locations: a box, a table cell, a page + quote) and, optionally, <b>the quote</b> itself. Only the '
        f'source is required, so a bare link with a quote is a valid Reference. The caller (the AI, the CRM, '
        f'ObjectDB) decides <i>what</i> to cite; the highlighter only shows it, as you asked.{c(7)}</p>'
        '<p>To show one, a server-side check (polite: robots.txt, one request a second per host) compares the live '
        f'source with what was cited:{c(7)}</p>')
    add(table(["Status", "Means", "The viewer shows"], [
        ['<span class="pill live">live</span>', "The live file or page matches exactly: same sha256, or every "
         "location resolves and the quote is found exactly once.",
         "The stored copy marked, “live · unchanged since …”, and a live link your browser highlights."],
        ['<span class="pill deviated">deviated</span>', "Reachable, but no longer an exact match (quote gone, found "
         "twice, or the file changed).", "The best stored copy marked, “page changed since …”."],
        ['<span class="pill dead">dead</span>', "Unreachable three times, a day apart. A robots.txt refusal reads "
         "“not checked” and never counts.", "The stored copy marked, “page unreachable”."],
        ['<span class="pill">link-only</span>', "Nothing stored and no exact live match.",
         "The address and quote: “nothing stored; can't show where”. Never a guessed mark."],
    ]))
    add('<p class="small">When the live side fails, the viewer falls back along the stored copies in this order:</p>'
        '<div class="chain" aria-label="Fallback order"><span class="step first">live</span><span class="arrow">→</span>'
        '<span class="step">html</span><span class="arrow">→</span><span class="step">snapshot</span>'
        '<span class="arrow">→</span><span class="step">archive</span><span class="arrow">→</span>'
        '<span class="step">pdf</span></div>'
        f'<p class="small muted">The mark is always drawn on a stored copy, never on a re-rendered live page; a '
        f're-render could drift from what was cited. The one real check so far: CubeMars and Mean Well both '
        f'<span class="pill live">live</span> (the quote found once; the PDF the same sha256).{c(1, 7, 9)}</p>')
    add('<h3>The CRM (Tycoon\'s bam-data)</h3><ul>'
        f'<li><b>147 References</b>, one per distinct web address, vault link or file across all records, kept as '
        f'Python records and projected to the §5 JSON.{c(1, 10)}</li>'
        f'<li><b>215 evidence entries</b> now carry <code>ref</code>, pinned wherever an entry cites exactly one '
        f'source; the original source strings are untouched.{c(1, 10)}</li>'
        f'<li><code>bam-data capture &lt;ref&gt; --keep html</code> saves a copy through <code>mdify capture</code> '
        f'(71f814c).{c(10)}</li></ul>')
    add('<h3>ObjectDB (bam-waste-objects)</h3><ul>'
        f'<li><code>refs/&lt;mix-id&gt;.json</code> files for 13 mixes, link-only today (no page + quote yet); '
        f'composition rows cite <code>&lt;mix-id&gt;-&lt;n&gt;</code>.{c(1, 11)}</li>'
        f'<li><b>Its check run found a real bug.</b> A <code>file://</code> address with <code>%20</code> in it (its '
        f'Apple%20Notes sources) read as missing. Fixed in source-highlighter 9a405fc: <code>file://</code> addresses '
        f'are percent-decoded as RFC 8089 says, bare paths stay literal; test written first; 81 tests.{c(1, 8)}</li>'
        '</ul></section>')

    # ------------------------------------------------------------ 7 · prior art
    add('<section id="prior-art"><h2><span class="n">7</span>Prior art</h2>'
        f'<p>Full note: {vault_link("Linked Sources — Off-the-Shelf Citation Viewers and NotebookLM, Prior Art (2026-11-05)")}.{c(12)}</p>'
        '<h3>What NotebookLM does</h3><ul>'
        f'<li>It highlights a passage of <b>its own extracted text</b>, never a box on the original page; a PDF\'s '
        f'graphs and images come through as blank space.{c(12)}</li>'
        f'<li>A web source is a text copy <b>frozen at import</b>; there is no live check.{c(12)}</li>'
        f'<li class="muted small">From Google\'s help pages and reviews; not run (no login). It was renamed Gemini '
        f'Notebook in July 2026.{c(12)}</li></ul>'
        '<h3>No product does the whole thing</h3><ul>'
        f'<li><b>Zotero</b>: exact highlights, but only on stored copies.{c(12)}</li>'
        f'<li><b>Hypothesis</b>: re-finds highlights on live pages, but keeps no copy.{c(12)}</li>'
        f'<li><b>Readwise Reader</b>: does both, but it is closed and consumer-only.{c(12)}</li></ul>'
        '<p>So the Source Highlighter stays small, and stock parts take the jobs they can:</p>')
    add(table(["Stock part", "Its job here", "Status"], [
        ["pdf.js <code>PDFViewer</code> + <code>PDFFindController</code>", "PDF paging, the text layer, page + quote "
         "locations", "Adopt; not done yet"],
        ["approx-string-match + Hypothesis's <code>matchQuote</code>", "Re-find a quote on a changed page",
         "Done, pinned"],
        ["SingleFile", "A faithful one-file offline copy", "Done: the display-only <code>single</code> level"],
        ["ReplayWeb.page", "Replay the full archive", "Done: the “Full archive” button"],
        ["Scoop (Harvard LIL)", "The recorder under <code>mdify capture</code>", "Not adopted: the trial said no"],
        ["Memento Time Travel", "“Archived near this date”", "Not used: dead in DNS"],
    ], row_classes=["", "win", "win", "win", "no", "no"]))
    add('</section>')

    # ------------------------------------------------------------ 8 · viewer round
    ranking = [("V4", "Peek", 87, True), ("V2", "Reader", 85, False), ("V1", "Lens", 83, True),
               ("V3", "Evidence table", 83, False), ("V5", "Provenance", 69, False)]
    add('<section id="viewer"><h2><span class="n">8</span>The viewer round</h2>'
        f'<p>You asked for five proposals for the standalone viewer. All five pass every hard gate; the AI ranking '
        f'is below (fragile: the top four sit within four points). Built ones are in blue.{c(13)}</p>'
        '<div class="rank" role="list">')
    for code, name, score, built in ranking:
        add(f'<div class="r{" built" if built else ""}" role="listitem"><span class="name"><b>{code}</b> {E(name)}'
            f'</span><span class="track"><span class="fill" style="display:block;width:{score}%"></span></span>'
            f'<span class="score">{score}</span></div>')
    add('</div>'
        f'<p>{file_link(VIEWER_GALLERY, "Open the five proposals")} (each one clickable, with its walkthrough and '
        f'scores).{c(13)}</p>'
        '<h3>What was built</h3><ul>'
        f'<li><b>Peek</b> (V4) inline, opening into <b>Lens</b> (V1) full page, with V5\'s fallback chain inside '
        f'Lens Details.{c(1, 8)}</li>'
        f'<li><code>source-highlighter check</code> writes each Reference\'s status; <code>source-highlighter '
        f'view</code> serves Lens and the Peek script.{c(8)}</li>'
        f'<li><b>Proof:</b> marks vs their Locations within 0.026 px over every kind; PDF quotes vs pdfplumber within '
        f'0.017 px; Details drags 340 → 460 px and stays 460 after reload; the pinned Peek panel\'s move and resize '
        f'are remembered; Peek works in a plain page on another origin and inside a copy of <code>mdify view</code> '
        f'with 0 console lines; 81 tests.{c(1)}</li></ul></section>')

    # ------------------------------------------------------------ 9 · capture trial
    add('<section id="capture"><h2><span class="n">9</span>Capture trial</h2>'
        f'<p>Our recorder (<code>mdify capture</code>) against two stock tools, on three real pages, one polite visit '
        f'each. <i>Replay</i> is the share of pixels that differ when the saved copy is reopened offline and compared '
        f'with our snapshot; lower is better.{c(14)}</p>')
    add(table(["Page", "Ours", "SingleFile", "Scoop"], [
        ["CubeMars", "6.1 MB · replay 0.01 % · 198 responses", "3.6 MB · one file", "13.3 MB · replay 0.13 % · 238"],
        ["eRob80", "7.3 MB · 0.00 % · 118", "7.6 MB", "8.5 MB · 0.34 % · 71 (partial: hit its 20 s limit)"],
        ["moteus", "285 KB · 0.00 %", "160 KB", "424 KB · 0.00 %"],
    ]))
    add('<ul>'
        f'<li><b>Ours: kept.</b> Best replay on every page, and the only one that measures word boxes and checks '
        f'robots.txt inside the visit.{c(14)}</li>'
        f'<li><b>SingleFile: adopted, for display only.</b> One file that opens with no network. But it inlines every '
        f'image, which makes our converter too slow on image-heavy pages (stopped after 12–15 min), so values keep '
        f'citing <code>source.html</code>.{c(14)}</li>'
        f'<li><b>Scoop: not adopted.</b> Worse completeness and replay here, no robots.txt or rate limit, no word '
        f'boxes; it would replace about 360 lines, not the 675 the prior-art note estimated.{c(14)}</li>'
        f'<li><b>ReplayWeb.page: adopted</b> behind the “Full archive” button: 0 requests off the machine when '
        f'replaying.{c(14)}</li></ul></section>')

    # ------------------------------------------------------------ 10 · decision surface
    add('<section id="decide"><h2><span class="n">10</span>Decisions</h2><h3>Done and proved</h3><ul>'
        f'<li>Sources app: add → queue → review → publish on real datasheets; journey 68/68 on the real server; '
        f'52 tests.{c(1, 4)}</li>'
        f'<li>Reference format (§5) and live-first status (§6) written; the CRM\'s 147 References and ObjectDB\'s 13 '
        f'mixes use it.{c(7, 10, 11)}</li>'
        f'<li>Peek → Lens, <code>check</code> and <code>view</code> built; marks within 0.026 px; one real check, '
        f'both sources live; 81 tests.{c(1, 8, 9)}</li>'
        f'<li>Capture trial measured on three pages; SingleFile and ReplayWeb.page in; markdownify\'s 157 non-harness '
        f'tests pass.{c(1, 14)}</li>'
        f'<li>Prior art filed as a vault note.{c(12)}</li></ul>'
        '<h3>Decided for you</h3>'
        '<p>Eight calls, each with its one-line undo, in <a href="#decided">section 4</a>.</p>'
        '<h3>Left</h3><ul>'
        f'<li><b>Running now:</b> ragged source tables in web pages fail <code>mdify check</code> rule 4 (eRob80); '
        f'<code>mdify view</code>\'s headline reads “27 lostnull”.{c(1)}</li>'
        '<li><b>Next:</b> adopt pdf.js\'s <code>PDFViewer</code>; Lens\'s auto / live / stored modes; quote search '
        'inside PDFs in the static form; OCR confidence into word scores; child bundles for iframes and images in web '
        f'pages.{c(1)}</li>'
        f'<li><b>Other agents\' side:</b> RobotParts wiring as a consumer of the Sources app; RobotParts\' phase-current '
        f'vocabulary (not landed).{c(1)}</li></ul>'
        '<h3>Needs you</h3>'
        '<div class="ask"><div class="q">The 15 values from Oct 29</div>'
        f'<p class="rec"><b>Recommend:</b> tick them ✓ / ✗ / ? in '
        f'{file_link(OCT29_REPORT, "the Oct 29 report, section 9", "#human-check")} and paste the list back.{c(15)}</p>'
        '<p class="def"><b>If you say nothing:</b> they stay unchecked and are never shown as verified.</p></div>'
        '<div class="ask"><div class="q">Push to a remote?</div>'
        '<p class="rec"><b>Recommend:</b> private GitHub for markdownify, datasheet-extractor, source-highlighter and '
        'ObjectDB; today they exist only on this disk.</p>'
        '<p class="def"><b>If you say nothing:</b> they stay local.</p></div>'
        '<h3>Deliberately not done</h3><ul>'
        '<li>No audit; those run only when you ask. Worth a <code>/llm-judge</code>: the extractor\'s checker, '
        'because it decides what RobotParts shows as verified.</li>'
        '<li>Nothing written into RobotParts.</li>'
        '<li>No CRM or ObjectDB data migrated by me; their agents own it.</li></ul></section>')

    # ------------------------------------------------------------ 11 · run
    add('<section id="run"><h2><span class="n">11</span>How to run each thing</h2>'
        '<p class="small muted">Each prints its local URL and opens no window.</p>')
    add(command("uv run --directory /home/bam/datasheet-extractor extract serve",
                "<b>The Sources app</b> — add sources, watch the queue, review and publish"))
    add(command(f"uv run --directory /home/bam/source-highlighter source-highlighter view {VIEWER_REFS} "
                f"--statuses {VIEWER_MEDIA / 'statuses.json'}",
                "<b>Lens</b> on the seven demo References from the hero, with the real check's statuses; "
                "<code>#ref=ref-2</code> opens the LRS-350 one"))
    add(command(f"uv run --directory /home/bam/markdownify mdify view {LRS350_BUNDLE}",
                "<b><code>mdify view</code></b> on the Mean Well LRS-350 bundle; a bundle with a stored archive shows a "
                "<i>Full archive</i> button"))
    add(command("uv run --directory /home/bam/markdownify python /home/bam/markdownify/docs/build_report_2026_11_05.py",
                "<b>Rebuild this page</b> — reuses its encoded videos; add <code>--fresh</code> to re-encode"))
    add('<p class="small">Earlier: '
        f'{file_link(OCT29_REPORT, "the Oct 29 report")} · '
        f'{file_link(REVIEW_GALLERY, "the Oct 29 review-view proposals")} · '
        f'{file_link(VIEWER_GALLERY, "the Nov 5 viewer proposals")}.</p></section>')

    # ------------------------------------------------------------ source index
    add('<section id="index"><h2>Source Index</h2><ol class="sources">')
    for number, (label, where) in enumerate(SOURCES, 1):
        link = ""
        if where.startswith("/home/bam/zach_brain/"):
            link = " · " + vault_link("open in Obsidian")
        elif where.endswith(".html") and where.startswith("/"):
            link = " · " + file_link(pathlib.Path(where), "open")
        add(f'<li id="src-{number}">{label} — <code>{E(where)}</code>{link}</li>')
    add('</ol><p class="small muted">Built by <code>/home/bam/markdownify/docs/build_report_2026_11_05.py</code>. Both '
        'videos and every image are inlined; the page needs no network.</p></section>')

    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"color-scheme\" content=\"light dark\">"
            "<title>Linked Sources Review</title>"
            f"<style>{CSS}</style></head><body><main>" + "".join(parts) + f"</main><script>{JS}</script></body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".html.tmp")
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(OUT)  # WHY: never leave a half-written page where the open fence points
    return OUT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fresh", action="store_true", help="re-encode both hero videos")
    arguments = parser.parse_args()
    path = build(arguments.fresh)
    print(f"{path}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
