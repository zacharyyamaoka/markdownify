"""The capture trial of 2026-11-05 (docs/research/2026-11-05-capture-trial.md): `mdify capture` vs SingleFile vs
Scoop on three pages, one polite visit per tool per page, then everything else offline.

    uv run --project /home/bam/markdownify python -I /home/bam/markdownify/tools/scoop/trial.py <stage> [slug...]

Stages that visit the site (each once per page, after `Fetcher.permit`): ours, single, scoop. Offline stages:
offline_single (single.html rendered with the network blocked), check_single and check_ours (`mdify check`),
replay_scoop (Scoop's WACZ through `mdify.capture_replay`). rwp_replay.py replays any of the archives through
ReplayWeb.page. Results go to $TRIAL_RESULTS (default ~/.cache/markdownify/trial-2026-11-05): <stage>/<slug>/ and
<stage>.json — raw evidence, kept out of git.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

from mdify import capture, capture_replay
from mdify.cache import Cache
from mdify.fetch import Fetcher

REPO = pathlib.Path(__file__).resolve().parents[2]
RESULTS = pathlib.Path(os.environ.get("TRIAL_RESULTS") or pathlib.Path.home() / ".cache/markdownify/trial-2026-11-05")
NODE = "/usr/bin/node"
CHROMIUM = "/home/bam/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"   # Playwright 1.64's, as the visit
PAGES = {
    "cubemars": "https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html",
    "erob80": "https://www.zeroerr.cn/eRob/eRob80.html",
    "moteus": "https://mjbots.github.io/moteus/reference/hardware/",
}


def save(stage, slug, result):
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{stage}.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[slug] = result
    path.write_text(json.dumps(data, indent=1))


def ours(slug, url):
    out = RESULTS / "ours" / slug
    started = time.perf_counter()
    captured = capture.capture(url, tuple(capture.LEVELS), out, log=print)
    seconds = time.perf_counter() - started
    replayed = capture_replay.replay(out, image_out=out / "replay.jpg")
    return {"seconds": round(seconds, 1), "final_url": captured.final_url,
            "sizes": {level.name: level.bytes for level in captured.levels},
            "archive_check_ok": captured.archive_check["ok"], "records": captured.archive_check["records"],
            "warnings": captured.warnings, "replay": {k: v for k, v in replayed.items() if k != "misses"},
            "replay_misses": len(replayed["misses"])}


def user_agent(slug):
    record = json.loads((RESULTS / "ours" / slug / "source.json").read_text())
    return record["retrievals"][-1]["user_agent"]


def single(slug, url):
    out = RESULTS / "single" / slug
    out.mkdir(parents=True, exist_ok=True)
    Fetcher(Cache()).permit(url)
    started = time.perf_counter()
    run = subprocess.run([NODE, str(REPO / "tools/singlefile/node_modules/single-file-cli/single-file-node.js"),
                          url, str(out / "single.html"), "--browser-executable-path", CHROMIUM,
                          "--browser-width", "1280", "--browser-height", "800", "--user-agent", user_agent(slug),
                          "--errors-file", str(out / "errors.txt")],
                         capture_output=True, text=True, timeout=300)
    seconds = time.perf_counter() - started
    (out / "cli.log").write_text(run.stdout + "\n---\n" + run.stderr)
    return {"seconds": round(seconds, 1), "returncode": run.returncode,
            "bytes": (out / "single.html").stat().st_size if (out / "single.html").exists() else None}


def scoop(slug, url):
    out = RESULTS / "scoop" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "attachments").mkdir(exist_ok=True)
    Fetcher(Cache()).permit(url)
    started = time.perf_counter()
    run = subprocess.run([NODE, str(REPO / "tools/scoop/node_modules/@harvard-lil/scoop/bin/cli.js"), url,
                          "-o", str(out / "source.wacz"), "--json-summary-output", str(out / "summary.json"),
                          "--export-attachments-output", str(out / "attachments"),
                          "--capture-window-x", "1280", "--capture-window-y", "800",
                          "--user-agent-suffix", " mdify-capture/0.1 (+https://robotparts.dev/)",
                          "--capture-video-as-attachment", "false", "--capture-certificates-as-attachment", "false",
                          "--provenance-summary", "true", "--public-ip-resolver-endpoint", "http://127.0.0.1:9/",
                          "--yt-dlp-path", "/bin/false", "--crip-path", "/bin/false",
                          "--proxy-port", "9417", "--log-level", "info"],
                         capture_output=True, text=True, timeout=600, cwd=REPO / "tools/scoop")
    seconds = time.perf_counter() - started
    (out / "cli.log").write_text(run.stdout + "\n---\n" + run.stderr)
    return {"seconds": round(seconds, 1), "returncode": run.returncode,
            "bytes": (out / "source.wacz").stat().st_size if (out / "source.wacz").exists() else None}




def offline_single(slug, url):
    """Render single.html with the network blocked; pixel difference from our snapshot.jpg."""
    from playwright.sync_api import sync_playwright

    from mdify import capture_browser

    out = RESULTS / "single" / slug
    blocked = []

    def answer(route, request):
        if request.url.startswith(("file:", "data:", "blob:")):
            route.continue_()
            return
        blocked.append(request.url)
        route.abort("internetdisconnected")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=capture_browser.VIEWPORT, device_scale_factor=1, service_workers="block",
                                      locale="en-US", timezone_id="UTC")
        context.route("**/*", answer)
        page = context.new_page()
        page.goto((out / "single.html").as_uri(), wait_until="load")
        capture_browser.settle(page)
        page.evaluate(capture_browser.SCROLL_JS, [capture_browser.SCROLL_STEP_PAUSE_MS, capture_browser.SCROLL_MAX_STEPS])
        capture_browser.settle(page)
        page.evaluate("() => window.scrollTo(0, 0)")
        image = page.screenshot(type="jpeg", quality=capture_browser.JPEG_QUALITY, full_page=True)
        browser.close()
    (out / "offline.jpg").write_bytes(image)
    result = capture_replay.compare(RESULTS / "ours" / slug / "snapshot.jpg", image)
    result["network_requests_blocked"] = len(blocked)
    result["blocked_examples"] = blocked[:5]
    return result


def check_single(slug, url):
    """Convert single.html as a source (`mdify <file>`), then `mdify check` the bundle."""
    out = RESULTS / "single" / slug
    bundle_dir = out / os.environ.get("BUNDLE_NAME", "bundle")
    started = time.perf_counter()
    convert = subprocess.run(["uv", "run", "--project", str(REPO), "mdify", str(out / "single.html"), "--out",
                              str(bundle_dir)], capture_output=True, text=True)
    seconds = time.perf_counter() - started
    check = subprocess.run(["uv", "run", "--project", str(REPO), "mdify", "check", str(bundle_dir)],
                           capture_output=True, text=True)
    kpis = json.loads((bundle_dir / "kpis.json").read_text()) if (bundle_dir / "kpis.json").exists() else None
    (out / "check.log").write_text(convert.stdout + convert.stderr + "\n---\n" + check.stdout + check.stderr)
    return {"convert_rc": convert.returncode, "convert_seconds": round(seconds, 1), "check_rc": check.returncode,
            "check_tail": (check.stdout + check.stderr)[-600:], "kpis": kpis}


def check_ours(slug, url):
    out = RESULTS / "ours" / slug
    check = subprocess.run(["uv", "run", "--project", str(REPO), "mdify", "check", str(out)], capture_output=True,
                           text=True)
    kpis = json.loads((out / "kpis.json").read_text())
    return {"check_rc": check.returncode, "check_tail": (check.stdout + check.stderr)[-600:], "kpis": kpis}




def replay_scoop(slug, url):
    """Scoop's WACZ through our own replay (capture_replay: every request answered from the archive, network blocked,
    the capture's clock), against our snapshot.jpg and against Scoop's own capture-time screenshot. Scoop stores bodies
    as sent on the wire (gzip/br), which warcio decodes on read, so the encoding headers are dropped before the page
    gets the decoded body."""
    import shutil
    import zipfile

    from mdify import capture_archive

    out = RESULTS / "scoop" / slug
    pages = [json.loads(line) for line in zipfile.ZipFile(out / "source.wacz").read("pages/pages.jsonl").decode().splitlines()[1:]]
    page = next(p for p in pages if p["url"].startswith("http"))
    ours_record = json.loads((RESULTS / "ours" / slug / "source.json").read_text())
    retrieval = dict(ours_record["retrievals"][-1], final_url=page["url"], retrieved_at=page["ts"])
    (out / "source.json").write_text(json.dumps({"retrievals": [retrieval]}))
    original = capture_archive.read_records

    def decoded(path):
        from warcio.archiveiterator import ArchiveIterator

        records = []
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not (name.startswith("archive/") and name.endswith((".warc", ".warc.gz"))):
                    continue
                with archive.open(name) as stream:
                    for record in ArchiveIterator(stream):
                        if record.rec_type != "response" or record.http_headers is None:
                            continue    # Scoop's attachments (file:///screenshot.png) have no HTTP head
                        records.append({
                            "url": record.rec_headers.get_header("WARC-Target-URI"),
                            "status": int(record.http_headers.get_statuscode() or 0),
                            "headers": [(n, v) for n, v in record.http_headers.headers if n.lower() not in
                                        ("content-encoding", "transfer-encoding", "content-length")],
                            "body": record.content_stream().read()})
        return records

    capture_archive.read_records = decoded
    try:
        shutil.copy(RESULTS / "ours" / slug / "snapshot.jpg", out / "snapshot.jpg")
        result = capture_replay.replay(out, image_out=out / "replay.jpg")
    finally:
        capture_archive.read_records = original
    own = capture_replay.compare(out / "attachments" / "screenshot.png", (out / "replay.jpg").read_bytes())
    misses = result.pop("misses")
    return {"vs_our_snapshot": result, "misses": len(misses), "miss_examples": misses[:5], "vs_scoop_screenshot": own}


if __name__ == "__main__":
    stage, slugs = sys.argv[1], sys.argv[2:] or list(PAGES)
    for slug in slugs:
        print(f"== {stage} {slug}", flush=True)
        result = globals()[stage](slug, PAGES[slug])
        print(json.dumps(result, indent=1), flush=True)
        save(stage, slug, result)
