"""`mdify <path-or-url>… [--out DIR]` writes a bundle per source; `mdify check <bundle>…` re-derives its invariants.

Several sources in one run share the loaded models, so the first conversion is timed cold and the rest warm
(kpis.json `time.cold`).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from .cache import display_path


def _convert(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mdify", description="Any digital source → markdown in which every word "
                                     "links back to where it sits in the source (CONTRACT.md).")
    parser.add_argument("sources", nargs="+", help="a local file or an http(s) URL (PDF, HTML, PNG, JPEG)")
    parser.add_argument("--out", type=pathlib.Path, help="write the bundle here instead of the cache "
                        "(~/.cache/markdownify/bundles/<sha256>/); one source only")
    arguments = parser.parse_args(argv)
    if arguments.out and len(arguments.sources) > 1:
        parser.error("--out takes one source")

    from . import bundle
    from .fetch import FetchError, FetchRefused

    status = 0
    for target in arguments.sources:
        try:
            built = bundle.build(target, arguments.out, log=lambda message: print(message, file=sys.stderr))
        except (FetchRefused, FetchError, bundle.NoConverter, FileNotFoundError) as error:
            print(f"mdify: {target}: {error}", file=sys.stderr)
            status = 1
            continue
        kpis = built.kpis
        time = kpis["time"]
        print(f"{display_path(built.directory)}")
        print(f"  coverage {_fmt(kpis['coverage'])}  traceability {_fmt(kpis['traceability'])}  "
              f"lost {_fmt(kpis['silently_lost'])}  unlinked {kpis['unlinked']}  inexact {kpis['inexact_words']}  "
              f"{time['pages']} pages in {time['seconds']} s ({'cold' if time['cold'] else 'warm'}, "
              f"models {time['model_load_seconds']} s)")
    return status


def _fmt(value) -> str:
    return "n/a" if value is None else str(value)


def _check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mdify check", description="Re-derive a bundle's invariants (CONTRACT §4) "
                                     "from its cached bytes; no model, no network. Exits 1 on any failure.")
    parser.add_argument("bundles", nargs="+", type=pathlib.Path)
    arguments = parser.parse_args(argv)

    from . import check

    status = 0
    for directory in arguments.bundles:
        results = check.check(directory)
        print(check.report(display_path(directory), results))
        if not all(result.ok for result in results):
            status = 1
    return status


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return _check(argv[1:])
    return _convert(argv)


if __name__ == "__main__":
    sys.exit(main())
