"""The `archive` capture level (CONTRACT §2.1): every response of the visit as WARC (ISO 28500) request/response
records, packaged as WACZ (Webrecorder's zip of WARCs + CDX index + pages.jsonl) by py-wacz's own `wacz create`.
ReplayWeb.page replays a WACZ in the browser from the file alone.

Two choices worth knowing:
- WHY decoded bodies, without Content-Encoding: the browser hands over each body already decompressed (gzip, br), so
  the record says so — Content-Encoding and Transfer-Encoding are renamed `X-Archive-Orig-…` (pywb's convention for
  headers it no longer honours) and Content-Length is the body actually stored. A replayer that honoured the original
  header would try to gunzip plain bytes.
- WHY `WARC-Payload-Digest: sha256:<hex>` (and the block digest the same way): hex sha256 is what source.json and
  the bundle's name already use, so the main document's record is found by the bundle's own sha256; warcio's default
  is base32 sha1.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import pathlib
import urllib.parse
import warnings
import zipfile

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capture_browser import Exchange, Visit

WARC_NAME = "source.warc.gz"
_DROPPED_ON_DECODE = {"content-encoding", "transfer-encoding"}


class _Sha256Hex:
    """warcio's digester interface (update + str), writing sha256 in hex."""

    def __init__(self):
        self.digest = hashlib.sha256()

    def update(self, data: bytes) -> None:
        self.digest.update(data)

    def __str__(self) -> str:
        return "sha256:" + self.digest.hexdigest()


def _writer(stream):
    from warcio.warcwriter import WARCWriter

    class Writer(WARCWriter):
        @staticmethod
        def _create_digester():
            return _Sha256Hex()

    return Writer(stream, gzip=True, warc_version="1.1")


def stored_headers(exchange: Exchange) -> list[tuple[str, str]]:
    """The response headers as stored (and as served to the page during the capture): bodies are decoded."""
    headers = []
    for name, value in exchange.response_headers:
        lowered = name.lower()
        if lowered in _DROPPED_ON_DECODE:
            headers.append((f"X-Archive-Orig-{name}", value))
        elif lowered == "content-length":
            continue
        else:
            headers.append((name, value))
    headers.append(("Content-Length", str(len(exchange.body))))
    return headers


def _request_line(exchange: Exchange) -> tuple[str, list[tuple[str, str]]]:
    parsed = urllib.parse.urlsplit(exchange.url)
    target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    headers = list(exchange.request_headers)
    if not any(name.lower() == "host" for name, _ in headers):
        headers.insert(0, ("Host", parsed.netloc))
    return f"{exchange.method} {target} HTTP/1.1", headers


def archived(visit: Visit) -> list[Exchange]:
    """The exchanges the archive holds: http(s), with the body the browser delivered. One whose body the browser
    no longer had is left out rather than stored as an empty 200 (a replay would serve the empty body)."""
    return [exchange for exchange in visit.exchanges
            if exchange.url.startswith(("http://", "https://")) and exchange.body_error is None]


def write_warc(visit: Visit, path: pathlib.Path) -> int:
    """Write every http(s) exchange of the visit as a request + response pair; returns the response count."""
    from warcio.statusandheaders import StatusAndHeaders

    count = 0
    with open(path, "wb") as stream:
        writer = _writer(stream)
        info = writer.create_warcinfo_record(path.name, {
            "software": "mdify capture (warcio)", "format": "WARC File Format 1.1",
            "browser": visit.browser, "http-header-user-agent": visit.user_agent, "isPartOf": visit.url})
        writer.write_record(info)
        for exchange in archived(visit):
            date = exchange.retrieved_at
            status_line = f"{exchange.status} {exchange.status_text}".strip()
            response = writer.create_warc_record(
                exchange.url, "response", payload=io.BytesIO(exchange.body), length=len(exchange.body),
                http_headers=StatusAndHeaders(status_line, stored_headers(exchange), protocol="HTTP/1.1"),
                warc_headers_dict={"WARC-Date": date})
            line, headers = _request_line(exchange)
            body = exchange.post_data or b""
            request = writer.create_warc_record(
                exchange.url, "request", payload=io.BytesIO(body), length=len(body),
                http_headers=StatusAndHeaders(line, headers, is_http_request=True),
                warc_headers_dict={"WARC-Date": date,
                                   "WARC-Concurrent-To": response.rec_headers.get_header("WARC-Record-ID")})
            writer.write_record(response)
            writer.write_record(request)
            count += 1
    return count


def write_wacz(visit: Visit, warc_path: pathlib.Path, wacz_path: pathlib.Path) -> None:
    """Package the WARC with `wacz create`, naming the captured page as the archive's one page."""
    pages = warc_path.with_name("pages.jsonl")
    lines = [{"format": "json-pages-1.0", "id": "pages", "title": "All Pages"},
             {"id": hashlib.sha256(visit.final_url.encode()).hexdigest()[:32], "url": visit.final_url,
              "ts": visit.retrieved_at, "title": visit.title or visit.final_url}]
    pages.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # surt and pyamf, imported by py-wacz, warn on import (py3.12+)
        from wacz.main import main as wacz_main
    # WHY capture its output: py-wacz prints progress to stdout, which `mdify capture` keeps for its size table;
    # it is shown only when packaging fails.
    progress = io.StringIO()
    with contextlib.redirect_stdout(progress):
        status = wacz_main(["create", "-o", str(wacz_path), str(warc_path), "-p", str(pages),
                            "--hash-type", "sha256", "--url", visit.final_url, "--title", visit.title or visit.final_url])
    pages.unlink()
    if status not in (None, 0) or not wacz_path.is_file():
        raise RuntimeError(f"wacz create failed ({status}) for {warc_path}: {progress.getvalue()[-2000:]}")


def fulfil_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    """Headers as Playwright's route.fulfill takes them: one value per name. WHY comma-joined (RFC 9110 §5.3) and
    set-cookie alone newline-joined: Chromium never commits a navigation fulfilled with a newline-joined repeat of any
    other header (measured on CubeMars' page: `cache-control`, `pragma` and `vary` each come twice)."""
    joined: dict[str, str] = {}
    for name, value in headers:
        if name.lower().startswith("x-archive-orig-"):
            continue
        separator = "\n" if name.lower() == "set-cookie" else ", "
        joined[name] = f"{joined[name]}{separator}{value}" if name in joined else value
    return joined


# --- reading an archive back -------------------------------------------------------------------------------------
def read_records(wacz_path: pathlib.Path) -> list[dict]:
    """Every response record in the WACZ's WARCs: {url, date, status, headers, body, payload_digest}. Read from the
    zip with warcio alone, so this is what a replayer would find, not what the capture remembers."""
    from warcio.archiveiterator import ArchiveIterator

    records = []
    with zipfile.ZipFile(wacz_path) as archive:
        for name in archive.namelist():
            if not (name.startswith("archive/") and name.endswith((".warc", ".warc.gz"))):
                continue
            with archive.open(name) as stream:
                for record in ArchiveIterator(stream):
                    if record.rec_type != "response":
                        continue
                    body = record.content_stream().read()
                    records.append({
                        "url": record.rec_headers.get_header("WARC-Target-URI"),
                        "date": record.rec_headers.get_header("WARC-Date"),
                        "status": int(record.http_headers.get_statuscode() or 0),
                        "headers": list(record.http_headers.headers),
                        "body": body,
                        "payload_digest": record.rec_headers.get_header("WARC-Payload-Digest"),
                        "warc": name})
    return records


def index_urls(wacz_path: pathlib.Path) -> list[str]:
    """The URLs in the WACZ's CDX index (what ReplayWeb.page looks requests up in)."""
    urls = []
    with zipfile.ZipFile(wacz_path) as archive:
        for name in archive.namelist():
            if not name.startswith("indexes/") or not name.endswith((".cdx", ".cdxj", ".cdx.gz")):
                continue
            data = archive.read(name)
            if name.endswith(".gz"):
                data = _gunzip_members(data)
            for line in data.decode("utf-8").splitlines():
                fields = line.split(" ", 2)      # CDXJ: <surt key> <timestamp> <json>; a key may hold "{"
                if len(fields) == 3:
                    urls.append(json.loads(fields[2]).get("url"))
    return urls


def _gunzip_members(data: bytes) -> bytes:
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        out.write(stream.read())
    return out.getvalue()


def verify(wacz_path: pathlib.Path, source_html: bytes, final_url: str, expected: list[tuple[str, int]]) -> dict:
    """Check the archive holds the page: the main document's bytes equal source.html, every response the visit
    recorded is there (url + status), each body hashes to its payload digest, and the CDX index lists them.
    `expected` is [(url, status)] of the visit's exchanges."""
    records = read_records(wacz_path)
    indexed = set(index_urls(wacz_path))
    main = [record for record in records if record["url"] == final_url and record["status"] == 200]
    main_equal = bool(main) and main[0]["body"] == source_html
    present = {(record["url"], record["status"]) for record in records}
    missing = [f"{url} ({status})" for url, status in expected if (url, status) not in present]
    unindexed = sorted({url for url, _ in expected} - indexed)
    bad_digests = [record["url"] for record in records
                   if record["payload_digest"] != "sha256:" + hashlib.sha256(record["body"]).hexdigest()]
    return {"records": len(records), "expected": len(expected), "main_document_equal": main_equal,
            "missing": missing, "unindexed": unindexed, "bad_digests": bad_digests,
            "ok": main_equal and not missing and not unindexed and not bad_digests}
