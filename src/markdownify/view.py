"""`markdownify view`: Markdownify's own page for one bundle — the source on the left (drawn by the Source Highlighter,
CONTRACT §9), the markdown on the right, every word linked both ways, and the KPIs it is graded by on top (§5, §6).

A stdlib ThreadingHTTPServer on 127.0.0.1, read-only towards the bundle:

    GET /                       the page (viewer/index.html)
    GET /static/<path>          the page's own modules and styles, and the pinned copies under viewer/vendor/
    GET /bundle                 {sha256, media_type, source, name, files} — what the page needs before it fetches
    GET /bundle/<file>          one file of the bundle directory (document.md, index.json, ledger.json, source.pdf, …)

It opens no browser window; it prints the URL. WHY: Zach's no-focus-steal rule — a tool never fronts a window.
"""

from __future__ import annotations

import json
import mimetypes
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .cache import source_file

VIEWER = pathlib.Path(__file__).with_name("viewer")
# The files that make a bundle viewable (CONTRACT §2); a source-only cache directory has just source.*.
REQUIRED = ("document.md", "index.json", "ledger.json", "kpis.json")
BUNDLE_FILE = re.compile(r"^[A-Za-z0-9_.-]+$")

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/markdown", ".md")


class NotABundle(Exception):
    """The directory is not a complete bundle (no source, or a file of CONTRACT §2 missing)."""


def check_bundle(directory: pathlib.Path) -> pathlib.Path:
    """The bundle's source file, after checking every file the page reads is there."""
    missing = [name for name in REQUIRED if not (directory / name).is_file()]
    source = source_file(directory)
    if source is None:
        missing.insert(0, "source.<ext>")
    if missing:
        raise NotABundle(f"{directory} is not a complete bundle (missing {', '.join(missing)})")
    return source


def display_name(directory: pathlib.Path, source: pathlib.Path) -> str:
    """What the page calls its source: the last URL it was fetched from, else the file it was read from, else the
    bundle's own file name (source.json's retrievals, CONTRACT §2)."""
    try:
        record = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return source.name
    retrievals = record.get("retrievals", [])
    for retrieval in reversed(retrievals):
        if retrieval.get("url"):
            return retrieval["url"].rstrip("/").rsplit("/", 1)[-1] or retrieval["url"]
    for retrieval in retrievals:
        local = retrieval.get("local_file")
        if local and pathlib.Path(local).parent.resolve() != directory.resolve():
            return pathlib.Path(local).name
    return source.name


def manifest(directory: pathlib.Path) -> dict:
    source = check_bundle(directory)
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    return {
        "sha256": index["source"]["sha256"],
        "media_type": index["source"]["media_type"],
        "source": source.name,
        "name": display_name(directory, source),
        "files": sorted(entry.name for entry in directory.iterdir() if entry.is_file()),
    }


def _inside(root: pathlib.Path, relative: str) -> pathlib.Path | None:
    """`relative` under `root`, or None when it is not a file there (`..` and symlinks out of it included)."""
    root = root.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "Server"

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # the page asks for a few dozen files on open; the terminal keeps the one line that matters (the URL)

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path: pathlib.Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/json":
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if path in ("/", "/index.html"):
            return self._file(VIEWER / "index.html")
        if path == "/bundle":
            body = json.dumps(manifest(self.server.bundle)).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        if path.startswith("/static/"):
            found = _inside(VIEWER, path[len("/static/"):])
            return self._file(found) if found else self._send(404, b"not found")
        if path.startswith("/bundle/"):
            name = path[len("/bundle/"):]
            found = _inside(self.server.bundle, name) if BUNDLE_FILE.match(name) else None
            return self._file(found) if found else self._send(404, b"not found")
        return self._send(404, b"not found")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bundle: pathlib.Path, port: int = 0) -> None:
        check_bundle(bundle)
        self.bundle = pathlib.Path(bundle).resolve()
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"
