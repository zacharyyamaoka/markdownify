"""Does a capture's archive really replay offline? Load the page in Chromium with every request answered from the
WACZ alone — anything the archive does not hold is aborted, so nothing reaches the network — repeat the capture's
scroll, take the same full-page screenshot, and compare it pixel by pixel with `snapshot.jpg`.

WHY our own replay and not ReplayWeb.page: ReplayWeb.page runs as a service worker behind a web page, which is a
second program to install and drive; answering requests straight from the WARC records tests the claim that
matters — the archive alone holds what the page needs — with the files and the browser already here. ReplayWeb.page
reads the same WACZ (CDX index + WARC records, checked by `capture_archive.verify`).
"""

from __future__ import annotations

import datetime
import io
import json
import pathlib

from . import capture_archive, capture_browser, capture_snapshot

# A pixel "changed" when a channel moved more than this (JPEG q80 noise on an unchanged page stays well under it).
CHANGED_LEVEL = 40


def replay(directory: pathlib.Path, *, image_out: pathlib.Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    directory = pathlib.Path(directory)
    record = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    retrieval = next(r for r in reversed(record["retrievals"]) if r.get("capture"))
    records = capture_archive.read_records(directory / "source.wacz")
    by_url: dict[str, dict] = {}
    for entry in records:   # a URL fetched twice (the page reloaded itself) is answered with its last response
        by_url[entry["url"]] = entry
    served, misses = [], []

    def answer(route, request):
        entry = by_url.get(request.url.split("#")[0])
        if entry is None:
            misses.append(request.url)
            route.abort("internetdisconnected")
            return
        served.append(request.url)
        route.fulfill(status=entry["status"], headers=capture_archive.fulfil_headers(entry["headers"]),
                      body=entry["body"])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport=capture_browser.VIEWPORT, device_scale_factor=1,
                                          user_agent=retrieval.get("user_agent"), service_workers="block",
                                          locale="en-US", timezone_id="UTC")
            context.route("**/*", answer)
            # WHY the capture's clock: pages bake the time into what they request (eRob80 re-navigates to
            # `?t=<now>` once its `t` is a minute old), so a replay at today's time asks for URLs no archive can hold.
            # ReplayWeb.page does the same: the page's Date starts at the archive's timestamp and runs on from there.
            context.clock.install(time=datetime.datetime.fromisoformat(retrieval["retrieved_at"].replace("Z", "+00:00")))
            page = context.new_page()
            page.goto(retrieval["final_url"], wait_until="load", timeout=capture_browser.NAVIGATION_TIMEOUT_MS)
            capture_browser.settle(page)
            page.evaluate(capture_browser.SCROLL_JS, [capture_browser.SCROLL_STEP_PAUSE_MS,
                                                       capture_browser.SCROLL_MAX_STEPS])
            capture_browser.settle(page)
            page.evaluate("() => window.scrollTo(0, 0)")
            image = page.screenshot(type="jpeg", quality=capture_browser.JPEG_QUALITY, full_page=True)
            context.close()
        finally:
            browser.close()
    if image_out is not None:
        pathlib.Path(image_out).write_bytes(image)
    result = compare(directory / capture_snapshot.IMAGE_NAME, image)
    result.update({"requests": len(served) + len(misses), "served": len(served), "misses": misses,
                   "records": len(records)})
    return result


def compare(snapshot_path: pathlib.Path, replay_jpeg: bytes) -> dict:
    """Pixel difference of two full-page JPEGs over their common area: the mean absolute channel difference (0–255)
    and the share of pixels with any channel off by more than CHANGED_LEVEL."""
    import numpy
    from PIL import Image

    with Image.open(snapshot_path) as first, Image.open(io.BytesIO(replay_jpeg)) as second:
        a, b = first.convert("RGB"), second.convert("RGB")
        width, height = min(a.width, b.width), min(a.height, b.height)
        left = numpy.asarray(a.crop((0, 0, width, height)), dtype=numpy.int16)
        right = numpy.asarray(b.crop((0, 0, width, height)), dtype=numpy.int16)
        sizes = {"capture_size": list(first.size), "replay_size": list(second.size)}
    difference = numpy.abs(left - right)
    changed = (difference.max(axis=2) > CHANGED_LEVEL)
    return {**sizes, "mean_abs_diff": round(float(difference.mean()), 3),
            "changed_share": round(float(changed.mean()), 5),
            "changed_rows": int(changed.any(axis=1).sum())}
