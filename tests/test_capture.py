"""`mdify capture` against a local site: one browser visit, every level measured, the kept ones kept, the archive read
back and replayed with the network blocked, the snapshot's word boxes checked against the browser's own element
boxes (an oracle outside the word walk), and `mdify check` passing on — and failing on a tampered — snapshot box.

The site is served on 127.0.0.1 by `http.server`; its robots.txt allows everything except /private/. Nothing here
touches the network.
"""

import gzip
import http.server
import json
import threading

import pytest

pytest.importorskip("playwright")
pytest.importorskip("warcio")
pytest.importorskip("wacz")

from mdify import capture, capture_archive, capture_replay, capture_snapshot, check, cli  # noqa: E402
from mdify.cache import Cache  # noqa: E402
from mdify.fetch import FetchRefused  # noqa: E402

PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>AK80-9 test page</title>
<link rel="stylesheet" href="/style.css"></head>
<body>
<header><nav><a href="/">Home</a> <a href="/products">Products</a></nav></header>
<main>
<h1>AK80-9 actuator</h1>
<p>Rated torque <b>9</b>Nm at 48&nbsp;V, quiet and light.</p>
<table id="specs"><tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Rated torque</td><td>9 Nm</td></tr>
<tr><td>Peak torque</td><td>18 Nm</td></tr></table>
<div class="spacer"></div>
<p><img src="/pic.png" alt="Front view of the actuator" loading="lazy" width="120" height="80"></p>
<p id="bottom">Bottom line text</p>
</main>
<script src="/app.js"></script>
</body></html>
"""
STYLE = b"body{font:16px/1.4 sans-serif;margin:24px;color:#111;background:#fff} .spacer{height:1600px} " \
        b"table{border-collapse:collapse} td,th{border:1px solid #888;padding:4px 10px}"
APP = b"document.body.insertAdjacentHTML('beforeend', '<p id=added>Added by script</p>');"
# A 2x2 red PNG.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73000000164944415478"
                    "9c63f8cfc0f01f0418c0ffff0300ff0bfe01e3bd6e1c0000000049454e44ae426082")


class Site(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Site.hits.append(self.path)
        routes = {"/page": (PAGE, "text/html; charset=utf-8"), "/style.css": (STYLE, "text/css"),
                  "/app.js": (APP, "application/javascript"), "/pic.png": (PNG, "image/png"),
                  "/robots.txt": (b"User-agent: *\nDisallow: /private/\n", "text/plain"),
                  "/hop": (b"<!doctype html><title>hop</title><p>Moving on</p>"
                           b"<script>setTimeout(() => location.replace('/page'), 50)</script>", "text/html")}
        if self.path == "/old":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path not in routes:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body, content_type = routes[self.path]
        encoding = None
        if self.path == "/style.css":   # gzip on the wire: the archive must store it decoded, and say so
            body, encoding = gzip.compress(body), "gzip"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def captured(site, tmp_path_factory):
    """One capture keeping every level, shared by the tests that read it."""
    root = tmp_path_factory.mktemp("capture")
    cache = Cache(root / "cache")
    out = root / "bundle"
    result = capture.capture(f"{site}/old", tuple(capture.LEVELS), out, cache=cache)
    return result, out, cache


def test_parse_keep():
    assert capture.parse_keep("html") == ("link", "html")
    assert capture.parse_keep("archive,html,snapshot") == ("link", "html", "snapshot", "archive")
    with pytest.raises(ValueError):
        capture.parse_keep("html,video")


def test_every_level_is_measured_and_kept(captured):
    result, out, _cache = captured
    assert [level.name for level in result.levels] == list(capture.LEVELS)
    for level in result.levels:
        assert level.bytes == sum((out / name).stat().st_size for name in level.files), level
        assert level.bytes > 0
    assert (out / "source.html").read_bytes() == PAGE
    assert result.final_url.endswith("/page")
    record = json.loads((out / "source.json").read_text())
    retrieval = record["retrievals"][-1]
    assert retrieval["url"].endswith("/old") and retrieval["final_url"].endswith("/page")
    assert retrieval["capture"]["kept"] == list(capture.LEVELS)
    assert retrieval["capture"]["requests"] >= 5           # redirect hop, page, css, js, png
    for name in ("document.md", "index.json", "ledger.json", "kpis.json"):
        assert (out / name).is_file()
    table = capture.size_table(result, dry_sizes=False)
    for name in capture.LEVELS:
        assert name in table


def test_archive_holds_every_response(captured):
    result, out, _cache = captured
    assert result.archive_check["ok"], result.archive_check
    records = capture_archive.read_records(out / "source.wacz")
    by_url = {record["url"]: record for record in records}
    page = by_url[result.final_url]
    assert page["body"] == (out / "source.html").read_bytes()
    assert page["payload_digest"].startswith("sha256:") and len(page["payload_digest"]) == 7 + 64
    style = next(record for url, record in by_url.items() if url.endswith("/style.css"))
    assert style["body"] == STYLE                                # stored decoded
    names = {name.lower() for name, _ in style["headers"]}
    assert "content-encoding" not in names and "x-archive-orig-content-encoding" in names
    assert any(url.endswith("/old") and record["status"] == 302 for url, record in by_url.items())
    assert any(url.endswith("/pic.png") for url in by_url)       # the lazy image, loaded by scrolling


def test_archive_replays_offline(captured, site):
    _result, out, _cache = captured
    before = len(Site.hits)
    replayed = capture_replay.replay(out)
    assert len(Site.hits) == before, "replay reached the site"
    assert replayed["misses"] == []
    assert replayed["capture_size"] == replayed["replay_size"]
    assert replayed["changed_share"] < 0.001, replayed


def test_single_renders_offline_from_one_file(captured):
    """The `single` level: SingleFile's second visit, saved as one HTML file that renders with every request off."""
    from playwright.sync_api import sync_playwright

    result, out, _cache = captured
    single = next(level for level in result.levels if level.name == "single")
    assert single.bytes == (out / "single.html").stat().st_size > len(PAGE)
    record = json.loads((out / "source.json").read_text())
    assert record["retrievals"][-1]["capture"]["single"]["tool"].startswith("single-file-cli ")
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.route("**/*", lambda route, request: (requests.append(request.url), route.abort())
                      if not request.url.startswith(("file:", "data:")) else route.continue_())
        page = context.new_page()
        page.goto((out / "single.html").as_uri())
        text = page.inner_text("body")
        margin = page.evaluate("getComputedStyle(document.body).marginLeft")
        image = page.evaluate("[...document.images].map(i => [i.src.slice(0, 15), i.naturalWidth])")
        browser.close()
    assert requests == []
    assert "Rated torque" in text and "Peak torque" in text
    assert margin == "24px"                                  # /style.css, inlined
    assert image == [["data:image/png;", 2]]                 # /pic.png, inlined


def test_snapshot_boxes_sit_inside_the_browsers_element_boxes(captured):
    """Oracle: the browser's own getBoundingClientRect of each table cell, from a separate page load."""
    from playwright.sync_api import sync_playwright

    result, out, _cache = captured
    snapshot = json.loads((out / capture_snapshot.WORDS_NAME).read_text())
    index = json.loads((out / "index.json").read_text())
    markdown = (out / "document.md").read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(result.final_url)
        cells = page.evaluate("""() => [...document.querySelectorAll('#specs td, #specs th')].map(cell => {
            const r = cell.getBoundingClientRect(); return [cell.textContent.trim(), r.left, r.top, r.right, r.bottom]; })""")
        browser.close()
    width, height = snapshot["width"], snapshot["height"]
    boxed = {}
    for word in index["words"]:
        grounding = word.get("grounding") or {}
        if "snapshot" in grounding:
            boxed.setdefault(markdown[word["range"]["start"]:word["range"]["end"]], []).append(grounding["snapshot"]["box"])
    for text, *rect in cells:
        x0, y0, x1, y1 = rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height
        for token in text.split():
            assert any(x0 <= box[0] and box[2] <= x1 and y0 <= box[1] and box[3] <= y1 for box in boxed[token]), (token, rect)
    counts = sum(1 for word in index["words"] if (word.get("grounding") or {}).get("snapshot"))
    linked = sum(1 for word in index["words"] if word.get("grounding"))
    assert counts == linked, f"{linked - counts} linked words without a snapshot box"


def test_check_accepts_snapshot_groundings_and_catches_a_moved_box(captured, tmp_path):
    _result, out, _cache = captured
    results = check.check(out)
    assert all(result.ok for result in results), check.report(str(out), results)

    import shutil
    tampered = tmp_path / "tampered"
    shutil.copytree(out, tampered)
    index = json.loads((tampered / "index.json").read_text())
    markdown = (tampered / "document.md").read_text()
    boxed = [word for word in index["words"] if (word.get("grounding") or {}).get("snapshot")]
    peak = next(word for word in boxed if markdown[word["range"]["start"]:word["range"]["end"]] == "Peak")
    bottom = next(word for word in boxed if markdown[word["range"]["start"]:word["range"]["end"]] == "Bottom")
    peak["grounding"]["snapshot"] = dict(bottom["grounding"]["snapshot"])
    (tampered / "index.json").write_text(json.dumps(index))
    results = check.check(tampered)
    faithful = results[0]
    assert not faithful.ok and any("Peak" in failure and "snapshot box holds" in failure for failure in faithful.failures)


def test_dry_sizes_keeps_nothing(site, tmp_path):
    cache = Cache(tmp_path / "cache")
    out = tmp_path / "bundle"
    result = capture.capture(f"{site}/page", ("html",), out, dry_sizes=True, cache=cache)
    assert result.directory is None and not out.exists()
    assert not any((tmp_path / "cache" / "bundles").iterdir())
    assert not list((tmp_path / "cache").glob(".capture-*"))
    assert all(level.bytes > 0 for level in result.levels)


def test_keep_html_only_deletes_the_rest(site, tmp_path):
    cache = Cache(tmp_path / "cache")
    result = capture.capture(f"{site}/page", ("html",), cache=cache)
    files = {path.name for path in result.directory.iterdir()}
    assert {"source.html", "source.json", "document.md", "index.json"} <= files
    assert not files & {"snapshot.jpg", "snapshot.json", "snapshot.pdf", "single.html", "source.wacz"}
    single = next(level for level in result.levels if level.name == "single")
    assert single.bytes is None                              # a second visit, made only when kept
    assert "not built" in capture.size_table(result, dry_sizes=False)
    index = json.loads((result.directory / "index.json").read_text())
    assert not any("snapshot" in (word.get("grounding") or {}) for word in index["words"])


def test_link_and_archive_without_html(site, tmp_path):
    cache = Cache(tmp_path / "cache")
    result = capture.capture(f"{site}/page", ("archive",), cache=cache)
    files = {path.name for path in result.directory.iterdir()}
    assert files == {"source.json", "source.wacz"}
    assert not cache.has(result.sha256)     # an archive is not a source.<ext>


def test_a_page_that_navigates_itself_is_captured_where_it_ends(site, tmp_path):
    """eRob's page replaces itself after load: the html level is the document on screen, not the first one."""
    cache = Cache(tmp_path / "cache")
    result = capture.capture(f"{site}/hop", ("html",), cache=cache)
    assert result.final_url.endswith("/page")
    assert (result.directory / "source.html").read_bytes() == PAGE


def test_robots_and_refused_hosts_stop_the_visit(site, tmp_path):
    cache = Cache(tmp_path / "cache")
    before = len(Site.hits)
    with pytest.raises(FetchRefused, match="disallows"):
        capture.capture(f"{site}/private/page", cache=cache)
    with pytest.raises(FetchRefused, match="McMaster"):
        capture.capture("https://www.mcmaster.com/91251A540/", cache=cache)
    assert [hit for hit in Site.hits[before:] if hit != "/robots.txt"] == []


def test_cli_prints_the_size_table(site, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MARKDOWNIFY_CACHE", str(tmp_path / "cache"))
    status = cli.main(["capture", f"{site}/page", "--dry-sizes"])
    output = capsys.readouterr().out
    assert status == 0
    assert "level" in output and "archive check PASS" in output and "nothing kept (--dry-sizes)" in output
    for name in capture.LEVELS:
        assert f"\n{name}" in output


def test_cli_help_says_when_single_makes_a_second_visit(capsys):
    with pytest.raises(SystemExit):
        cli.main(["capture", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "single" in help_text and "second visit" in help_text, help_text
    assert "--dry-sizes" in help_text.split("second visit", 1)[1][:200], help_text
