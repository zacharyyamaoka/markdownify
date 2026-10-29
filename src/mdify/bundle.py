"""The core: a source (a path or a URL) → a bundle directory (CONTRACT §2).

The core knows converters only through the protocol in `mdify.converters`. It picks one by the source's media type,
importing it lazily — so the PDF path works while the HTML converter does not exist yet, and a converter's heavy
dependencies (docling, torch) load only when that converter runs. Then it writes the bundle: the bytes and their
fetch record, the markdown, the span index, and the ledger and KPIs, which it computes itself from the converter's
Conversion plus the converter's independent inventory (`mdify.ledger`), so every converter is graded the same way.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import pathlib
import shutil
import time

from . import __version__
from . import ledger as ledger_module
from .cache import Cache, extension_of, source_file, write_json
from .converters import Conversion, Source
from .fetch import Fetcher, ingest_local_file

INDEX_SCHEMA = "markdownify/index@0"
# Media type → the module that converts it. WHY a table of names, not imports: importing is what costs (docling
# loads torch), and a module that is missing or broken must not stop the others.
CONVERTERS = {
    "application/pdf": "mdify.pdf",
    "text/html": "mdify.html",
    "image/png": "mdify.image",
    "image/jpeg": "mdify.image",
}
_warmed: set[str] = set()


class NoConverter(Exception):
    """No converter can read this media type (unknown, or its module does not import)."""


def converter_for(media_type: str):
    name = CONVERTERS.get(media_type)
    if name is None:
        raise NoConverter(f"no converter for {media_type}")
    try:
        module = importlib.import_module(name)
    except ImportError as error:
        raise NoConverter(f"{media_type}: {name} does not import ({error})") from error
    if media_type not in getattr(module, "MEDIA_TYPES", ()):
        raise NoConverter(f"{name} does not declare {media_type} in MEDIA_TYPES")
    return module


@dataclasses.dataclass
class Built:
    directory: pathlib.Path
    kpis: dict


def resolve(target: str, cache: Cache, log=None) -> tuple[str, str, str | None]:
    """(sha256, media type, url) of a path or URL, with its bytes in the cache."""
    if target.startswith(("http://", "https://")):
        fetched = Fetcher(cache, log=log).fetch(target)
        return fetched.sha256, fetched.media_type, fetched.url
    fetched = ingest_local_file(cache, pathlib.Path(target))
    return fetched.sha256, fetched.media_type, None


def index_of(conversion: Conversion, source: Source, pipeline: str, markdown_sha256: str) -> dict:
    return {
        "schema": INDEX_SCHEMA,
        "tool": {"name": "mdify", "version": __version__, "pipeline": pipeline},
        "source": {"sha256": source.sha256, "media_type": source.media_type},
        "markdown_sha256": markdown_sha256,
        "range_units": "unicode_codepoints",
        "pages": conversion.pages,
        "blocks": conversion.blocks,
        "words": conversion.words,
        "dropped": conversion.dropped,
    }


def build(target: str, out: pathlib.Path | None = None, *, cache: Cache | None = None, log=None) -> Built:
    cache = cache or Cache()
    sha256, media_type, url = resolve(target, cache, log)
    module = converter_for(media_type)
    directory = pathlib.Path(out) if out else cache.bundle_dir(sha256)
    directory.mkdir(parents=True, exist_ok=True)
    cached = cache.source_path(sha256)
    source_path = directory / f"source.{extension_of(media_type)}"
    if directory.resolve() != cache.bundle_dir(sha256).resolve():
        if not source_path.is_file():
            shutil.copyfile(cached, source_path)
        shutil.copyfile(cache.bundle_dir(sha256) / "source.json", directory / "source.json")
    source = Source(path=source_path, sha256=sha256, media_type=media_type, url=url)

    started = time.perf_counter()
    cold = module.__name__ not in _warmed
    if cold and hasattr(module, "warm_up"):
        module.warm_up()
    _warmed.add(module.__name__)
    loaded = time.perf_counter()
    conversion = module.convert(source)
    finished = time.perf_counter()
    timing = {"seconds": round(finished - loaded, 3), "cold": cold,
              "model_load_seconds": round(loaded - started, 3)}

    markdown = conversion.markdown.replace("\r\n", "\n").replace("\r", "\n")
    markdown_bytes = markdown.encode("utf-8")
    index = index_of(conversion, source, getattr(module, "PIPELINE", "A"), hashlib.sha256(markdown_bytes).hexdigest())
    inventory = module.inventory(source)
    ledger = ledger_module.compute(index, markdown, inventory, f"{module.__name__}.inventory")
    kpis = ledger_module.kpis_of(index, ledger, timing)

    (directory / "document.md").write_bytes(markdown_bytes)
    write_json(directory / "index.json", index)
    write_json(directory / "ledger.json", ledger)
    write_json(directory / "kpis.json", kpis)
    return Built(directory=directory, kpis=kpis)


def load(directory: pathlib.Path) -> tuple[dict, dict, dict, str, Source]:
    """(source.json, index.json, ledger.json, document.md, Source) of a bundle directory."""
    directory = pathlib.Path(directory)
    record = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    ledger = json.loads((directory / "ledger.json").read_text(encoding="utf-8"))
    markdown = (directory / "document.md").read_bytes().decode("utf-8")
    path = source_file(directory)
    urls = [retrieval.get("url") for retrieval in record.get("retrievals", []) if retrieval.get("url")]
    source = Source(path=path, sha256=record["sha256"], media_type=record["media_type"], url=urls[-1] if urls else None)
    return record, index, ledger, markdown, source
