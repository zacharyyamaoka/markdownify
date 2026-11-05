"""Replay a bundle's source.wacz through ReplayWeb.page (mdify view's archive page, vendored) in headless Chromium,
with every request off 127.0.0.1 aborted; screenshot the replayed page full-page and compare with a reference image.

uv run --project /home/bam/markdownify python -I rwp_replay.py <bundle dir> <reference image> <out.jpg> [url] [ts]

url and ts are the page to open and its 14-digit capture time (pages/pages.jsonl of the WACZ, or source.json's last
retrieval). FIRST_SCREEN=1 compares the first 1280×800 screen only. A Scoop archive needs a bundle directory around it
(mdify view serves only complete bundles): copy ours' document.md, index.json, ledger.json, kpis.json, source.html
beside Scoop's source.wacz.
"""
import json
import os
import pathlib
import sys
import threading
import time
import urllib.parse

from playwright.sync_api import sync_playwright

from mdify import capture_browser, capture_replay, view


def main(bundle, reference, out, url=None, ts=None):
    server = view.Server(pathlib.Path(bundle))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    off_host = []

    def gate(route, request):
        host = urllib.parse.urlsplit(request.url).hostname
        if request.url.startswith(("data:", "blob:")) or host == "127.0.0.1":
            route.continue_()
            return
        off_host.append(request.url)
        route.abort("internetdisconnected")

    params = urllib.parse.urlencode({k: v for k, v in (("url", url), ("ts", ts)) if v})
    started = time.perf_counter()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport=capture_browser.VIEWPORT, device_scale_factor=1, locale="en-US",
                                          timezone_id="UTC")
            context.route("**/*", gate)
            page = context.new_page()
            page.goto(f"{server.url}static/archive.html?{params}", wait_until="load")
            deadline = time.time() + 120
            replay_frame = None
            while time.time() < deadline and replay_frame is None:
                for frame in page.frames:
                    if "mp_/" in frame.url:
                        replay_frame = frame
                page.wait_for_timeout(500)
            if replay_frame is None:
                page.screenshot(path=str(pathlib.Path(out).with_suffix(".shell.png")))
                raise SystemExit(f"no replay frame; frames: {[f.url for f in page.frames]}")
            try:
                replay_frame.wait_for_load_state("load", timeout=60_000)
            except Exception:
                pass
            embedded_url = replay_frame.url
            page.screenshot(path=str(pathlib.Path(out).with_suffix(".embed.png")))
            # The replayed page alone, laid out at the capture's width. WHY in an iframe of a page of our own and not
            # top level: a replayed page opened top level redirects itself to replayweb.page (wombat's top-frame
            # redirect). The frame is then stretched to the page's full height, which is what a full-page
            # screenshot does to the viewport too.
            holder = context.new_page()
            holder.goto(f"{server.url}bundle", wait_until="load")
            holder.evaluate("""(src) => { document.open(); document.write('<!doctype html><body style="margin:0">'
                + '<iframe id=r style="border:0;display:block;width:1280px;height:800px"></iframe>'); document.close();
                document.getElementById('r').src = src; }""", embedded_url)
            holder.wait_for_timeout(1000)
            frame = next(f for f in holder.frames if "mp_/" in f.url)
            frame.wait_for_load_state("load", timeout=120_000)
            try:
                frame.wait_for_load_state("networkidle", timeout=capture_browser.SETTLE_TIMEOUT_MS)
            except Exception:
                pass
            frame.evaluate(capture_browser.SCROLL_JS, [capture_browser.SCROLL_STEP_PAUSE_MS,
                                                       capture_browser.SCROLL_MAX_STEPS])
            holder.wait_for_timeout(1000)
            frame.evaluate("() => window.scrollTo(0, 0)")
            height = frame.evaluate("() => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)")
            if os.environ.get("FIRST_SCREEN"):
                height = 800        # the first screen only: no stretching, so vh-sized blocks keep their size
            holder.set_viewport_size({"width": 1280, "height": height})
            holder.evaluate("(h) => { document.getElementById('r').style.height = h + 'px'; }", height)
            holder.wait_for_timeout(1500)
            image = holder.locator("#r").screenshot(type="jpeg", quality=capture_browser.JPEG_QUALITY)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    pathlib.Path(out).write_bytes(image)
    result = capture_replay.compare(pathlib.Path(reference), image)
    result.update({"replay_url": embedded_url, "off_host_requests_aborted": len(off_host),
                   "off_host_examples": off_host[:5], "seconds": round(time.perf_counter() - started, 1)})
    return result


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:]), indent=1))
