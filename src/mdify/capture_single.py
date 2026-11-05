"""The `single` capture level (CONTRACT §2.1): the rendered page saved by SingleFile's stock CLI (`single-file-cli`,
pinned in tools/singlefile/) as one self-contained HTML file — CSS, images and fonts inlined, scripts removed, and a
CSP that blocks every fetch — so it renders offline with nothing beside it. A display copy, like snapshot.jpg:
`source.html` stays the bytes values cite.

WHY a second visit, built only when asked: SingleFile drives a browser of its own, so this level costs the site a
second page view, where every other level comes free from the one visit `mdify.capture_browser` makes. It goes through
the same gate first (`Fetcher.permit`: refused hosts, robots.txt, one request per second per host), it never contacts
a refused host from inside the page (`--blocked-url-pattern`), and it identifies itself with the visit's own user
agent. What it cannot do is re-check a frame's or a redirect's robots.txt inside the page, as the main visit does.

WHY a separate process: SingleFile is AGPL-3.0; run unmodified as its own program, its output files are ours.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import time

from . import capture_browser
from .fetch import REFUSED_HOSTS, Fetcher
from .html import NODE

FILE_NAME = "single.html"
TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "singlefile"
CLI = TOOL / "node_modules" / "single-file-cli" / "single-file-node.js"
TIMEOUT_S = 300                 # SingleFile's own load and capture limits are 60 s each; this only stops a hang


@dataclasses.dataclass
class Saved:
    path: pathlib.Path
    seconds: float
    version: str
    warnings: list[str]


def version() -> str:
    import json

    return json.loads((CLI.parent / "package.json").read_text(encoding="utf-8"))["version"]


def save(url: str, out: pathlib.Path, fetcher: Fetcher, *, user_agent: str, log=None) -> Saved:
    """Visit `url` with SingleFile (after `fetcher.permit`) and write the page to `out`."""
    from playwright.sync_api import sync_playwright

    log = log or (lambda message: None)
    if not CLI.is_file():
        raise RuntimeError(f"SingleFile is not installed: run `pnpm --dir {TOOL} install`")
    url = fetcher.permit(url)
    with sync_playwright() as playwright:
        chromium = playwright.chromium.executable_path   # the browser the main visit used, not a second download
    blocked = [rf"^https?://([^/]*\.)?{re.escape(domain)}(:\d+)?/" for domain in REFUSED_HOSTS]
    command = [NODE, str(CLI), url, str(out), "--browser-executable-path", chromium,
               "--browser-width", str(capture_browser.VIEWPORT["width"]),
               "--browser-height", str(capture_browser.VIEWPORT["height"]), "--user-agent", user_agent,
               "--filename-conflict-action", "overwrite"]
    for pattern in blocked:
        command += ["--blocked-url-pattern", pattern]
    started = time.perf_counter()
    run = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False, cwd=TOOL)
    seconds = time.perf_counter() - started
    if run.returncode != 0 or not out.is_file():
        raise RuntimeError(f"SingleFile failed ({run.returncode}) on {url}: {(run.stderr or run.stdout)[-2000:]}")
    warnings = [line.strip() for line in (run.stdout + run.stderr).splitlines() if line.strip()]
    log(f"capture: SingleFile saved {url} in {seconds:.1f} s ({out.stat().st_size} bytes)")
    return Saved(path=out, seconds=seconds, version=version(), warnings=warnings)
