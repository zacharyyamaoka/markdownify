"""The one browser visit behind `mdify capture` (CONTRACT §2.1): open the page in headless Chromium, record every
response as it arrives, scroll to the bottom as a reader would, then measure every visible word's box and take the
full-page JPEG and the PDF. Nothing here writes files; `mdify.capture` turns a `Visit` into the capture levels.

WHY a real browser and not the fetcher: the snapshot and the archive are "what a reader saw", which only exists after
the page's CSS, scripts and lazy images have run. The fetcher's rules still hold for the page itself — `mdify.capture`
asks `Fetcher.permit` (refused hosts, robots.txt, one request per second per host) before the visit — and every
document navigation inside the visit (a redirect hop, a frame) is checked against robots.txt again here. The page's
own sub-resources (CSS, JS, images, fonts) are fetched when the page asks for them, unthrottled: that is one page
view, not a crawl.

Recording: every request the page makes goes through one route handler that fetches it, records status, headers
and body, and answers the page with exactly those bytes (`visit.record` says why). A redirect's target, which
Playwright does not route, is recorded when it finishes (`visit.on_ended`).

Word boxes come from the same walk the HTML inventory makes (`html_tool/mdify_html.mjs` `Page.inventory`): the same
invisible tags, the same markup-only hiding, words joined across inline elements and split at every other element,
each keyed by its xpath and its ordinal among the words of that xpath. WHY the same walk: the browser parses the
delivered bytes with the same HTML5 algorithm jsdom uses, so for a page its scripts did not rebuild the keys line up
one to one with the inventory's words, and `mdify.capture_snapshot` joins a converter word to its box by xpath and
offset instead of by guessing from text.
"""

from __future__ import annotations

import dataclasses
import http
import time
import urllib.parse

from .cache import iso_z, utc_now
from .fetch import FetchRefused, Fetcher, host_of, refused_reason

VIEWPORT = {"width": 1280, "height": 800}   # WHY fixed: a replay must lay the page out exactly as the capture did
JPEG_QUALITY = 80
NAVIGATION_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 10_000                  # network idle after load / after scrolling, at most this long
SCROLL_STEP_PAUSE_MS = 250
SCROLL_MAX_STEPS = 80                       # 64 000 px at 800 px a step; a longer page keeps its unscrolled tail
USER_AGENT_SUFFIX = "mdify-capture/0.1 (+https://robotparts.dev/)"


@dataclasses.dataclass
class Exchange:
    """One request the page made and the response it got, as the browser saw them."""
    url: str
    method: str
    resource_type: str
    request_headers: list[tuple[str, str]]
    post_data: bytes | None
    status: int
    status_text: str
    response_headers: list[tuple[str, str]]
    body: bytes
    retrieved_at: str                       # UTC, when the response arrived
    body_error: str | None = None           # the browser no longer had the body (never archived as if empty)
    main_document: bool = False             # a navigation of the page itself (not a frame, not a sub-resource)


@dataclasses.dataclass
class Visit:
    url: str                                # as asked for (normalised)
    final_url: str                          # the main document's URL after redirects
    status: int
    content_type: str | None
    retrieved_at: str
    user_agent: str
    browser: str
    main_body: bytes                        # the main document's response body, exactly as delivered
    exchanges: list[Exchange]
    requests: int                           # every request the page made (data: URLs excluded by the browser)
    failed: list[str]                       # requests that never got a response (blocked, refused, network error)
    blocked: list[str]                      # requests this module refused (robots.txt, refused hosts)
    screenshot_jpeg: bytes
    pdf: bytes
    measured: dict                          # the word walk: {css_width, css_height, walked, words, images}
    title: str


# The inventory walk of html_tool/mdify_html.mjs, run in the live page, measuring as it goes. Keep the two in step:
# INVISIBLE_TAGS, INLINE_TAGS and the inline display:none rule are copied from there.
_WALK_JS = r"""
() => {
  const INVISIBLE = new Set(['head', 'title', 'script', 'style', 'noscript', 'template', 'iframe', 'object',
    'embed', 'canvas', 'video', 'audio', 'datalist', 'desc', 'defs', 'symbol', 'metadata', 'clipPath', 'mask',
    'marker', 'pattern', 'linearGradient', 'radialGradient']);
  const INLINE = new Set(['a', 'abbr', 'b', 'bdi', 'bdo', 'big', 'cite', 'code', 'data', 'del', 'dfn', 'em',
    'font', 'i', 'ins', 'kbd', 'label', 'mark', 'nobr', 'q', 's', 'samp', 'small', 'span', 'strike', 'strong', 'sub',
    'sup', 'time', 'tt', 'u', 'var', 'wbr']);
  const STYLE_HIDDEN = /(^|;)\s*display\s*:\s*none\s*(!important)?\s*(;|$)/i;
  const WHITESPACE = /\s/;
  const hidden = (element) => {
    const tag = element.localName;
    if (INVISIBLE.has(tag)) return true;
    if (element.hasAttribute('hidden')) return true;
    if (tag === 'input' && (element.getAttribute('type') || '').toLowerCase() === 'hidden') return true;
    return STYLE_HIDDEN.test(element.getAttribute('style') || '');
  };
  // xpaths exactly as the inventory writes them: [n] only when a sibling shares the name.
  const xpaths = new Map();
  const visit = (parent, prefix) => {
    const counts = new Map();
    for (const child of parent.children) counts.set(child.localName, (counts.get(child.localName) || 0) + 1);
    const seen = new Map();
    for (const child of parent.children) {
      const position = (seen.get(child.localName) || 0) + 1;
      seen.set(child.localName, position);
      xpaths.set(child, counts.get(child.localName) > 1 ? `${prefix}/${child.localName}[${position}]` : `${prefix}/${child.localName}`);
    }
  };
  visit(document, '');
  for (const element of document.querySelectorAll('*')) visit(element, xpaths.get(element));

  const root = document.documentElement;
  const cssWidth = Math.max(root.scrollWidth, document.body ? document.body.scrollWidth : 0);
  const cssHeight = Math.max(root.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const scrollX = window.scrollX, scrollY = window.scrollY;

  // What of an element's area a reader can see: not visibility:hidden / opacity 0, and inside every ancestor that
  // clips its overflow (a carousel's off-screen slides, a collapsed accordion). Cached per element.
  const shownCache = new Map(), clipCache = new Map();
  const shown = (element) => {
    if (!shownCache.has(element)) {
      shownCache.set(element, element.checkVisibility
        ? element.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })
        : true);
    }
    return shownCache.get(element);
  };
  const clipOf = (element) => {
    if (!element || element === root || element === document.body) return [0, 0, cssWidth, cssHeight];
    if (clipCache.has(element)) return clipCache.get(element);
    let clip = clipOf(element.parentElement);
    const style = getComputedStyle(element);
    if (style.position === 'fixed') clip = [0, 0, cssWidth, cssHeight];
    if (clip && (style.overflowX !== 'visible' || style.overflowY !== 'visible')) {
      const r = element.getBoundingClientRect();
      const own = [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY];
      clip = [Math.max(clip[0], own[0]), Math.max(clip[1], own[1]), Math.min(clip[2], own[2]), Math.min(clip[3], own[3])];
      if (clip[0] >= clip[2] || clip[1] >= clip[3]) clip = null;
    }
    clipCache.set(element, clip);
    return clip;
  };
  const union = (a, b) => (a ? (b ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] : a) : b);
  const measureSegment = (node, start, end) => {
    const parent = node.parentElement;
    if (!parent || !shown(parent)) return null;
    const clip = clipOf(parent);
    if (!clip) return null;
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, end);
    let box = null;
    for (const r of range.getClientRects()) {
      if (r.width <= 0 || r.height <= 0) continue;
      const x0 = Math.max(r.left + scrollX, clip[0]), y0 = Math.max(r.top + scrollY, clip[1]);
      const x1 = Math.min(r.right + scrollX, clip[2]), y1 = Math.min(r.bottom + scrollY, clip[3]);
      if (x0 < x1 && y0 < y1) box = union(box, [x0, y0, x1, y1]);
    }
    return box;
  };
  // A word written across several text nodes ("<b>9</b>Nm") keeps each node's box as a part, [characters, box],
  // so a markdown word covering only some of them gets those parts' box, not a share of the whole.
  const measure = (segments) => {
    const parts = segments.map(([node, start, end]) => [end - start, measureSegment(node, start, end)]);
    const box = parts.reduce((total, part) => union(total, part[1]), null);
    return [box, segments.length > 1 ? parts : null];
  };

  const words = [];
  const ordinals = new Map();
  let walked = 0;
  let current = null;
  const flush = () => {
    if (!current) return;
    const element = current.firstNode.parentElement;
    let common = element;
    if (current.lastNode !== current.firstNode) {
      while (common && !common.contains(current.lastNode)) common = common.parentElement;
    }
    const xpath = xpaths.get(common || element);
    const ordinal = ordinals.get(xpath) || 0;
    ordinals.set(xpath, ordinal + 1);
    walked++;
    const [box, parts] = measure(current.segments);
    if (box) words.push(parts ? [current.text, xpath, ordinal, box, parts] : [current.text, xpath, ordinal, box]);
    current = null;
  };
  const body = document.body || root;
  const stack = [[body, false]];
  while (stack.length) {
    const [node, leaving] = stack.pop();
    if (node.nodeType === 3) {
      const data = node.data;
      if (!data.trim()) continue;
      for (let unit = 0; unit < data.length; unit++) {
        const char = data[unit];
        if (WHITESPACE.test(char)) { flush(); continue; }
        if (!current) current = { text: '', firstNode: node, segments: [] };
        const last = current.segments[current.segments.length - 1];
        if (last && last[0] === node && last[2] === unit) last[2] = unit + 1;
        else current.segments.push([node, unit, unit + 1]);
        current.text += char;
        current.lastNode = node;
      }
      continue;
    }
    if (node.nodeType !== 1) continue;
    const inline = INLINE.has(node.localName);
    if (leaving) { if (!inline) flush(); continue; }
    if (hidden(node)) continue;
    if (!inline) flush();
    stack.push([node, true]);
    for (let index = node.childNodes.length - 1; index >= 0; index--) stack.push([node.childNodes[index], false]);
  }
  flush();

  // Images with alt text: the converter writes alt as a caption, so those words point at the image's box.
  const images = [];
  for (const image of document.querySelectorAll('img[alt]')) {
    const alt = image.getAttribute('alt');
    if (!alt.trim() || !shown(image)) continue;
    const clip = clipOf(image.parentElement);
    const r = image.getBoundingClientRect();
    if (!clip || r.width <= 0 || r.height <= 0) continue;
    const box = [Math.max(r.left + scrollX, clip[0]), Math.max(r.top + scrollY, clip[1]),
                 Math.min(r.right + scrollX, clip[2]), Math.min(r.bottom + scrollY, clip[3])];
    if (box[0] < box[2] && box[1] < box[3]) images.push([alt, xpaths.get(image), box]);
  }
  return { css_width: cssWidth, css_height: cssHeight, walked, words, images };
}
"""

SCROLL_JS = r"""
async ([pause, maxSteps]) => {
  // Scroll a viewport at a time until the bottom stops moving away (lazy images and infinite lists load as they
  // come into view), then back to the top, where the full-page screenshot and the word boxes are taken.
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let steps = 0;
  for (; steps < maxSteps; steps++) {
    const before = window.scrollY;
    window.scrollBy(0, window.innerHeight);
    await sleep(pause);
    const bottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
    if (bottom || window.scrollY === before) break;
  }
  window.scrollTo(0, 0);
  await sleep(pause);
  return steps;
}
"""


def _headers_array(entries) -> list[tuple[str, str]]:
    """Playwright's header list without HTTP/2 pseudo-headers (":authority"), which have no HTTP/1.1 line."""
    return [(entry["name"], entry["value"]) for entry in entries if not entry["name"].startswith(":")]


def _status_text(status: int, given: str) -> str:
    if given:
        return given
    try:
        return http.HTTPStatus(status).phrase       # HTTP/2 carries no reason phrase
    except ValueError:
        return ""


def visit(url: str, fetcher: Fetcher, *, log=None) -> Visit:
    """Open `url` once (it must already have passed `fetcher.permit`) and return everything the levels need."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from . import capture_archive

    log = log or (lambda message: None)
    exchanges: list[Exchange] = []
    failed: list[str] = []
    blocked: list[str] = []
    requests = 0
    outstanding: dict[int, str] = {}        # requests the page made that have not ended yet (recorded or failed)
    routed: set[int] = set()                # requests the route handler recorded itself

    def on_request(request):
        nonlocal requests
        requests += 1
        outstanding[id(request)] = request.url

    def on_ended(request):
        """The fallback recorder, for requests the route never saw: Playwright routes only the first URL of a
        redirect chain, so a redirect's target (the page itself, after http→https or a trailing slash) is fetched
        by the browser and recorded here — its body read as soon as the sync API gets to it."""
        outstanding.pop(id(request), None)
        if id(request) in routed:
            return
        response = request.response()
        if response is None or not request.url.startswith(("http://", "https://")):
            return
        body, body_error = b"", None
        try:
            body = response.body()
        except PlaywrightError as error:
            body_error = str(error).splitlines()[0]
        exchanges.append(Exchange(
            url=request.url, method=request.method, resource_type=request.resource_type,
            request_headers=_headers_array(request.headers_array()), post_data=request.post_data_buffer,
            status=response.status, status_text=_status_text(response.status, response.status_text),
            response_headers=_headers_array(response.headers_array()), body=body, retrieved_at=iso_z(utc_now()),
            body_error=body_error, main_document=_is_main_navigation(request)))

    def on_failed(request):
        outstanding.pop(id(request), None)
        failed.append(f"{request.url} ({request.failure})")

    def record(route, request):
        """Every request goes through here. Refused hosts are never contacted, from any part of the page; every
        document navigation (the page, a redirect hop, a frame) must also pass robots.txt, as the fetcher's own
        redirects do. Everything else is fetched here and the page is answered with exactly the bytes recorded.

        WHY record in the route and not on `requestfinished`: the sync API runs event handlers late, and a page that
        navigates itself (eRob80's does) has dropped the earlier bodies by then — 28 of its responses came out as
        200 with no body, its logo and its product-overview frame among them. Fetching in the route has every body
        in hand before the page sees it, and the page sees what the archive holds, so a replay is the capture."""
        target = request.url
        if not target.startswith(("http://", "https://")):
            route.continue_()
            return
        reason = refused_reason(target)
        if reason is None and request.resource_type == "document":
            allowed, why = fetcher.allowed(target)
            reason = None if allowed else why
        if reason is not None:
            blocked.append(f"{target} ({reason})")
            route.abort("blockedbyclient")
            return
        routed.add(id(request))
        try:
            # max_redirects=0: a redirect goes back to the browser, which follows it itself (see on_ended).
            response = route.fetch(max_redirects=0, timeout=NAVIGATION_TIMEOUT_MS)
            body = response.body()
        except PlaywrightError as error:
            route.abort("failed")
            failed.append(f"{target} ({str(error).splitlines()[0]})")
            return
        exchange = Exchange(
            url=target, method=request.method, resource_type=request.resource_type,
            request_headers=_headers_array(request.headers_array()), post_data=request.post_data_buffer,
            status=response.status, status_text=_status_text(response.status, response.status_text),
            response_headers=_headers_array(response.headers_array), body=body, retrieved_at=iso_z(utc_now()),
            main_document=_is_main_navigation(request))
        exchanges.append(exchange)
        outstanding.pop(id(request), None)   # recorded: the page may still leave before it finishes loading
        location = next((value for name, value in exchange.response_headers if name.lower() == "location"), None)
        if 300 <= exchange.status < 400 and location and request.resource_type == "document":
            # The browser follows this redirect without asking the route again, so its target is gated here.
            hop = urllib.parse.urljoin(target, location)
            reason = refused_reason(hop)
            if reason is None:
                allowed, why = fetcher.allowed(hop)
                reason = None if allowed else why
            if reason is not None:
                blocked.append(f"{hop} ({reason}; redirected from {target})")
                route.abort("blockedbyclient")
                return
        route.fulfill(status=exchange.status,
                      headers=capture_archive.fulfil_headers(capture_archive.stored_headers(exchange)), body=body)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            default_agent = browser.new_page().evaluate("navigator.userAgent")
            # WHY append, not replace: the page should be the page any reader's Chromium gets, and the site should
            # still be able to tell who visited — the same courtesy the fetcher's own user agent gives.
            user_agent = f"{default_agent.replace('HeadlessChrome', 'Chrome')} {USER_AGENT_SUFFIX}"
            # WHY block service workers: a worker answers requests the page would otherwise make, out of the
            # page's sight, so they would be missing from the archive (and replay would depend on the worker).
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, user_agent=user_agent,
                                          service_workers="block", locale="en-US", timezone_id="UTC")
            page = context.new_page()
            page.on("request", on_request)
            page.on("requestfinished", on_ended)
            page.on("requestfailed", on_failed)
            page.route("**/*", record)
            started = time.perf_counter()
            try:
                response = page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
            except PlaywrightError as error:
                message = str(error).splitlines()[0]
                if "Download is starting" in message:
                    raise FetchRefused(f"{url} is not a web page (the browser downloads it); "
                                       f"use `mdify {url}` for a PDF or an image") from error
                raise
            if response is None:
                raise FetchRefused(f"{url}: the browser got no response")
            settle(page)
            steps = page.evaluate(SCROLL_JS, [SCROLL_STEP_PAUSE_MS, SCROLL_MAX_STEPS])
            settle(page)
            page.evaluate("() => window.scrollTo(0, 0)")
            # WHY from the recorded exchanges and not goto's response: a page may navigate itself after load (eRob's
            # does), and then goto's body is gone and is not the document on screen. The main document is the last
            # navigation of the page itself, the one the snapshot and the word boxes describe.
            main = _main_document(exchanges, page.url)
            if main is None:
                raise FetchRefused(f"{url}: the browser kept no body for the page it ended on ({page.url})")
            if main.url != response.url:
                log(f"capture: the page navigated itself from {response.url} to {main.url}; capturing {main.url}")
            main_body, final_url, status = main.body, main.url, main.status
            content_type = next((value for name, value in main.response_headers if name.lower() == "content-type"), None)
            retrieved_at = main.retrieved_at
            measured = page.evaluate(_WALK_JS)
            screenshot = page.screenshot(type="jpeg", quality=JPEG_QUALITY, full_page=True)
            pdf = page.pdf(print_background=True)
            title = page.title()
            page.unroute("**/*")
            log(f"capture: visited {final_url} in {time.perf_counter() - started:.1f} s, scrolled {steps} screens: "
                f"{requests} requests ({len({host_of(exchange.url) for exchange in exchanges})} hosts), "
                f"{len(exchanges)} responses recorded, {len(failed)} failed, {len(blocked)} blocked, "
                f"{len(outstanding)} unfinished")
            for pending in list(outstanding.values())[:5]:
                log(f"capture:   unfinished {pending[:160]}")
            context.close()
            browser_name = f"Chromium {browser.version}"
        finally:
            browser.close()
    return Visit(url=url, final_url=final_url, status=status, content_type=content_type, retrieved_at=retrieved_at,
                 user_agent=user_agent, browser=browser_name, main_body=main_body, exchanges=exchanges,
                 requests=requests, failed=failed, blocked=blocked, screenshot_jpeg=screenshot, pdf=pdf,
                 measured=measured, title=title)


def _is_main_navigation(request) -> bool:
    """A navigation of the page itself. (Playwright raises for a navigation whose frame does not exist yet: a popup.)"""
    from playwright.sync_api import Error as PlaywrightError
    try:
        return request.is_navigation_request() and request.frame.parent_frame is None
    except PlaywrightError:
        return False


def _main_document(exchanges: list[Exchange], page_url: str) -> Exchange | None:
    """The last navigation of the page itself that has a body, preferring the URL the page ended on."""
    documents = [exchange for exchange in exchanges if exchange.main_document and exchange.body]
    on_screen = [exchange for exchange in documents if exchange.url == page_url.split("#")[0]]
    return (on_screen or documents or [None])[-1]


def settle(page) -> None:
    """Wait for the network to go quiet, but never forever: a page that polls or streams is captured as it is."""
    from playwright.sync_api import Error as PlaywrightError
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except PlaywrightError:
        pass
