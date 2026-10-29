"""Markdownify's cache: every source it read, by sha256, in the directory that becomes that source's bundle.

Adapted from robotparts' `tools/ingestor/cache.py` (same author, same rules). WHY outside the repository by default
(`~/.cache/markdownify`, or `$MARKDOWNIFY_CACHE`):
- the inputs are makers' datasheets, most of them not redistributable, so they are never committed;
- a reviewer must still be able to re-check a link against the exact bytes after the git worktree that made it is
  gone (worktrees are swept) and after a peer's `git stash -u` (which takes untracked files inside a checkout).

Layout:

    bundles/<sha256>/source.<ext>   the bytes exactly as fetched (or as read from a local file)
    bundles/<sha256>/source.json    {sha256, bytes, media_type, retrievals: [{url, final_url, retrieved_at,
                                     http_status, ...}]} — robotparts' blob sidecar shape
    bundles/<sha256>/…              the rest of the bundle (CONTRACT §2), written by `mdify.bundle`
    fetches.jsonl                   append-only log of every request the fetcher made (robots.txt included)
    robots/<host>.json              robots.txt as last read, per host; robots/<host>.last the last request's time

WHY the blob lives in its bundle (robotparts keeps `blobs/` apart): a bundle is keyed by the source's sha256 too, so
one directory per source keeps the bytes and every file derived from them side by side, with nothing to copy.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import tempfile

EXTENSIONS = {"application/pdf": "pdf", "text/html": "html", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def default_root() -> pathlib.Path:
    configured = os.environ.get("MARKDOWNIFY_CACHE")
    if configured:
        return pathlib.Path(configured).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return pathlib.Path(base) / "markdownify"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso_z(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_path(path: pathlib.Path) -> str:
    """A path as a reader on another machine of Zach's can follow it: $HOME written as ~."""
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def extension_of(media_type: str) -> str:
    return EXTENSIONS.get(media_type, "bin")


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def write_json(path: pathlib.Path, value) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def source_file(bundle: pathlib.Path) -> pathlib.Path | None:
    """The `source.<ext>` of a bundle directory (not source.json, not a web capture's archive)."""
    for candidate in sorted(bundle.glob("source.*")):
        if candidate.name not in ("source.json", "source.warc.gz", "source.wacz"):
            return candidate
    return None


class Cache:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = pathlib.Path(root) if root is not None else default_root()
        for name in ("bundles", "robots"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # --- locking ---------------------------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self, name: str):
        """An exclusive lock shared by every process using this cache (politeness and append-only logs)."""
        with open(self.root / f".{name}.lock", "a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    # --- sources ---------------------------------------------------------------------------------
    def bundle_dir(self, sha256: str) -> pathlib.Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
            raise ValueError(f"not a sha256: {sha256!r}")
        return self.root / "bundles" / sha256

    def source_path(self, sha256: str) -> pathlib.Path | None:
        return source_file(self.bundle_dir(sha256)) if self.bundle_dir(sha256).is_dir() else None

    def has(self, sha256: str) -> bool:
        return self.source_path(sha256) is not None

    def read(self, sha256: str) -> bytes:
        """The cached bytes, after checking they still hash to their name (a reviewer's first check)."""
        path = self.source_path(sha256)
        if path is None:
            raise FileNotFoundError(f"no cached source {sha256}")
        data = path.read_bytes()
        actual = sha256_bytes(data)
        if actual != sha256:
            raise ValueError(f"cached source {sha256} is corrupt: its bytes hash to {actual}")
        return data

    def meta(self, sha256: str) -> dict | None:
        path = self.bundle_dir(sha256) / "source.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def put(self, data: bytes, media_type: str, retrieval: dict) -> str:
        """Store bytes under their sha256 and record one more retrieval of them; returns the sha256."""
        sha256 = sha256_bytes(data)
        bundle = self.bundle_dir(sha256)
        path = bundle / f"source.{extension_of(media_type)}"
        with self.lock("bundles"):
            if not path.is_file():
                atomic_write(path, data)
            meta = self.meta(sha256) or {"sha256": sha256, "bytes": len(data), "media_type": media_type, "retrievals": []}
            meta["retrievals"].append(retrieval)
            write_json(bundle / "source.json", meta)
        return sha256

    def latest_retrieval(self, url: str) -> tuple[str, dict] | None:
        """The newest cached retrieval of `url` (by its requested URL), as (sha256, retrieval)."""
        best = None
        for meta_path in (self.root / "bundles").glob("*/source.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            for retrieval in meta.get("retrievals", []):
                if retrieval.get("url") == url and (best is None or retrieval["retrieved_at"] > best[1]["retrieved_at"]):
                    best = (meta["sha256"], retrieval)
        return best

    # --- logs ------------------------------------------------------------------------------------
    def append_jsonl(self, name: str, record: dict) -> None:
        with self.lock(name):
            with open(self.root / name, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
