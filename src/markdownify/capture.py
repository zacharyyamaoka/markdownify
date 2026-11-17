"""`markdownify capture <url>`: Markdownify's web capture (CONTRACT §2.1). One browser visit builds every capture level,
prints each one's measured size, keeps the levels the caller chose and deletes the rest; then the HTML converter
runs on `source.html` as usual, and when the snapshot is kept each word also gets its box on it (§3.3).

WHY a menu with sizes (Zach, 2026-10-29): "it's not either or. we should just provide all the options and then provide
the estimate of the storage size, so then the user can pick." So every level is built and measured on every run,
also the ones about to be thrown away — the size printed is the size of this page, not an average.

| level    | files                         | what it gives                                             |
|----------|-------------------------------|-----------------------------------------------------------|
| link     | source.json                   | where and when (always kept: it is the record of the rest) |
| html     | source.html                   | the main document's bytes exactly as delivered             |
| snapshot | snapshot.jpg + snapshot.json  | the full page as a JPEG + every visible word's box on it   |
| pdf      | snapshot.pdf                  | the browser's print of the page, with a text layer          |
| single   | single.html                   | the rendered page as one self-contained HTML file (SingleFile) |
| archive  | source.wacz                   | every response of the visit, replayable offline            |

The exception to "built on every run" is `single`: SingleFile drives a browser of its own, so it is a second visit to
the site, made only when `single` is kept or on --dry-sizes (`markdownify.capture_single` says why). Otherwise its row reads
"not built".

Default `--keep`: html,snapshot. CONTRACT §2.1's policy — `html` always, `snapshot` + `archive` for every page a kept
value cites, `html` only for bulk scraping — is the caller's, who knows which pages a value cites; the command can't.

WHY no rendered-DOM file in the `html` level: a serialised DOM is no server's bytes, so a value cited from it could not
be re-checked against any retrieval. What the page's scripts built is kept the honest ways — as measured words on the
snapshot and as the scripts themselves in the archive. When the delivered HTML holds far fewer visible words than the
rendered page showed (a page that builds itself with JS), the capture says so, because the converter reads only the
delivered HTML.
"""

from __future__ import annotations

import dataclasses
import gzip
import pathlib
import shutil
import sys
import tempfile

from . import capture_archive, capture_single, capture_snapshot
from .cache import Cache, display_path, sha256_bytes, write_json
from .converters import Source
from .fetch import FetchError, Fetcher, media_type_of

LEVELS = ("link", "html", "snapshot", "pdf", "single", "archive")
DEFAULT_KEEP = ("html", "snapshot")
LEVEL_FILES = {
    "link": ("source.json",),
    "html": ("source.html",),
    "snapshot": (capture_snapshot.IMAGE_NAME, capture_snapshot.WORDS_NAME),
    "pdf": ("snapshot.pdf",),
    "single": (capture_single.FILE_NAME,),
    "archive": ("source.wacz",),
}
# A page whose rendered text has this many times the delivered HTML's visible words was built by its scripts.
JS_BUILT_RATIO = 2.0


@dataclasses.dataclass
class Level:
    name: str
    files: tuple[str, ...]
    bytes: int | None                   # None: not built this run (`single` when neither kept nor --dry-sizes)
    note: str = ""


@dataclasses.dataclass
class Captured:
    url: str
    final_url: str
    sha256: str
    levels: list[Level]
    kept: tuple[str, ...]
    directory: pathlib.Path | None      # None for --dry-sizes
    archive_check: dict | None
    built: object | None = None         # markdownify.bundle.Built when the html level was kept and converted
    warnings: list[str] = dataclasses.field(default_factory=list)


def parse_keep(text: str) -> tuple[str, ...]:
    chosen = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in LEVELS]
    if unknown:
        raise ValueError(f"unknown level {', '.join(unknown)} (levels: {', '.join(LEVELS)})")
    # link is the record every other level hangs off, so it is always kept.
    return tuple(name for name in LEVELS if name in chosen or name == "link")


def capture(url: str, keep: tuple[str, ...] = DEFAULT_KEEP, out: pathlib.Path | None = None, *,
            dry_sizes: bool = False, cache: Cache | None = None, log=None, reader: bool = False) -> Captured:
    from . import capture_browser, html

    log = log or (lambda message: None)
    keep = parse_keep(",".join(keep))
    cache = cache or Cache()
    fetcher = Fetcher(cache, log=log)
    url = fetcher.permit(url)
    visit = capture_browser.visit(url, fetcher, log=log)
    if visit.status != 200:
        raise FetchError(f"{visit.final_url}: HTTP {visit.status}")
    media_type = media_type_of(visit.content_type, visit.main_body)
    if media_type != "text/html":
        raise FetchError(f"{visit.final_url}: {media_type} is not a web page; use `markdownify {url}` for it")
    sha256 = sha256_bytes(visit.main_body)
    warnings = []

    staging = pathlib.Path(tempfile.mkdtemp(dir=cache.root, prefix=".capture-"))
    try:
        (staging / "source.html").write_bytes(visit.main_body)
        image = visit.screenshot_jpeg
        (staging / capture_snapshot.IMAGE_NAME).write_bytes(image)
        words = capture_snapshot.words_file(visit.measured, image)
        (staging / capture_snapshot.WORDS_NAME).write_bytes(capture_snapshot.dump(words))
        if (words["width"], words["height"]) != (words["css_width"], words["css_height"]):
            warnings.append(f"the screenshot is {words['width']}×{words['height']} px but the page measured "
                            f"{words['css_width']}×{words['css_height']} CSS px; boxes are scaled to the image")
        (staging / "snapshot.pdf").write_bytes(visit.pdf)
        work = staging / "warc"
        work.mkdir()
        warc = work / capture_archive.WARC_NAME
        responses = capture_archive.write_warc(visit, warc)
        capture_archive.write_wacz(visit, warc, staging / "source.wacz")
        shutil.rmtree(work)
        expected = [(exchange.url, exchange.status) for exchange in capture_archive.archived(visit)]
        unread = [exchange.url for exchange in visit.exchanges if exchange.body_error is not None]
        if unread:
            warnings.append(f"{len(unread)} responses are not archived: the browser no longer had their bodies "
                            f"(e.g. {unread[0]})")
        archive_check = capture_archive.verify(staging / "source.wacz", visit.main_body, visit.final_url, expected)
        single = None
        if "single" in keep or dry_sizes:
            single = capture_single.save(visit.final_url, staging / capture_single.FILE_NAME, fetcher,
                                         user_agent=visit.user_agent, log=log)
            warnings += [f"SingleFile: {line}" for line in single.warnings]

        delivered = len(html.visible_page(Source(path=staging / "source.html", sha256=sha256, media_type=media_type,
                                                 url=url)).words)
        rendered = visit.measured["walked"]
        if rendered > JS_BUILT_RATIO * max(delivered, 1):
            warnings.append(f"the page builds itself with JS: the rendered page showed {rendered} words, the delivered "
                            f"HTML holds {delivered}; the converter reads only the delivered HTML (the snapshot and "
                            f"the archive keep what the scripts built)")

        sizes = {name: sum((staging / file).stat().st_size for file in LEVEL_FILES[name] if (staging / file).exists())
                 for name in LEVELS if name != "link" and (name != "single" or single is not None)}
        retrieval = {"url": url, "final_url": visit.final_url, "retrieved_at": visit.retrieved_at,
                     "http_status": visit.status, "content_type": visit.content_type, "user_agent": visit.user_agent,
                     "capture": {"browser": visit.browser, "requests": visit.requests, "responses": responses,
                                 "failed": len(visit.failed), "blocked": len(visit.blocked), "kept": list(keep),
                                 "sizes": dict(sizes), "rendered_words": rendered, "delivered_words": delivered}}
        if single is not None:
            retrieval["capture"]["single"] = {"tool": f"single-file-cli {single.version}",
                                              "seconds": round(single.seconds, 1)}
        record = {"sha256": sha256, "bytes": len(visit.main_body), "media_type": media_type, "retrievals": [retrieval]}
        write_json(staging / "source.json", record)
        sizes["link"] = (staging / "source.json").stat().st_size

        gzipped = len(gzip.compress(visit.main_body, 9))
        levels = [
            Level("link", LEVEL_FILES["link"], sizes["link"]),
            Level("html", LEVEL_FILES["html"], sizes["html"], f"{human(gzipped)} gzipped"),
            Level("snapshot", LEVEL_FILES["snapshot"], sizes["snapshot"],
                  f"{words['width']}×{words['height']} px, {len(words['words'])} words boxed"),
            Level("pdf", LEVEL_FILES["pdf"], sizes["pdf"]),
            Level("single", LEVEL_FILES["single"], sizes.get("single"),
                  f"SingleFile, a second visit ({single.seconds:.0f} s)" if single
                  else "a second visit; --keep single builds it"),
            Level("archive", LEVEL_FILES["archive"], sizes["archive"],
                  f"{responses} responses, {human(sum(len(e.body) for e in visit.exchanges))} of bodies"),
        ]
        captured = Captured(url=url, final_url=visit.final_url, sha256=sha256, levels=levels, kept=keep,
                            directory=None, archive_check=archive_check, warnings=warnings)
        if dry_sizes:
            return captured

        directory = pathlib.Path(out) if out else cache.bundle_dir(sha256)
        directory.mkdir(parents=True, exist_ok=True)
        captured.directory = directory
        if "html" in keep:
            # The cache gets the bytes and their retrieval exactly as `markdownify <url>` would; build() copies to --out.
            cache.put(visit.main_body, media_type, retrieval)
        else:
            _write_record(directory / "source.json", record, retrieval)
        for name in keep:
            if name in ("link", "html"):
                continue
            for file in LEVEL_FILES[name]:
                shutil.move(staging / file, directory / file)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if "html" in keep:
        from . import bundle

        captured.built = bundle.build(url, out, cache=cache, log=log, reader=reader,
                                      resolved=(sha256, media_type, url))
    return captured


def _write_record(path: pathlib.Path, record: dict, retrieval: dict) -> None:
    """source.json without its source.<ext> (the `link` level alone): one more retrieval of the same bytes."""
    import json

    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing.setdefault("retrievals", []).append(retrieval)
        record = existing
    write_json(path, record)


def human(count: int) -> str:
    for unit, size in (("MB", 1_000_000), ("KB", 1_000)):
        if count >= size:
            return f"{count / size:.1f} {unit}"
    return f"{count} B"


def size_table(captured: Captured, dry_sizes: bool) -> str:
    """The menu: every level's files and measured size, and whether this run kept it."""
    rows = [("level", "files", "bytes", "size", "", "kept")]
    for level in captured.levels:
        if level.bytes is None:
            rows.append((level.name, " + ".join(level.files), "", "—", level.note, "not built"))
            continue
        kept = "dry run" if dry_sizes else ("kept" if level.name in captured.kept else "deleted")
        rows.append((level.name, " + ".join(level.files), str(level.bytes), human(level.bytes), level.note, kept))
    kept_total = sum(level.bytes for level in captured.levels if level.name in captured.kept and level.bytes)
    every = sum(level.bytes for level in captured.levels if level.bytes)
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = ["  ".join(cell.rjust(widths[column]) if column in (2, 3) else cell.ljust(widths[column])
                       for column, cell in enumerate(row)).rstrip() for row in rows]
    lines.append(f"every level built {human(every)}; " + ("nothing kept (--dry-sizes)" if dry_sizes
                                                    else f"kept {', '.join(captured.kept)}: {human(kept_total)}"))
    return "\n".join(lines)


def archive_line(check: dict) -> str:
    if check["ok"]:
        return (f"archive check PASS: {check['records']} response records, all {check['expected']} recorded responses "
                f"present and indexed, payload digests match, main document == source.html")
    problems = []
    if not check["main_document_equal"]:
        problems.append("main document differs from source.html")
    for key in ("missing", "unindexed", "bad_digests"):
        if check[key]:
            problems.append(f"{len(check[key])} {key.replace('_', ' ')} (e.g. {check[key][0]})")
    return "archive check FAIL: " + "; ".join(problems)


def main_report(captured: Captured, dry_sizes: bool, stream=None) -> None:
    stream = stream or sys.stdout
    print(captured.final_url, file=stream)
    print(size_table(captured, dry_sizes), file=stream)
    if captured.archive_check is not None:
        print(archive_line(captured.archive_check), file=stream)
    for warning in captured.warnings:
        print(f"warning: {warning}", file=stream)
    if captured.directory is not None:
        print(display_path(captured.directory), file=stream)
