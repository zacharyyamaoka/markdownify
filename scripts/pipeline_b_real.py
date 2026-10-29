"""Pipelines A, B and AB on real makers' PDFs, where there is no truth but the ledger still grades every bundle
against pdfplumber's inventory.

    uv run python scripts/pipeline_b_real.py run a --out DIR                 # A, in one process: cold, then warm
    uv run python scripts/pipeline_b_real.py run b --model claude --out DIR
    uv run python scripts/pipeline_b_real.py run ab --model claude --out DIR
    uv run python scripts/pipeline_b_real.py table --out DIR [--save scripts/pipeline_b_results/real-pdfs.json]

The files are the cached Mean Well LRS-350 sheet (4 pages) and the first 4 pages of TI's TPS54331, cut with pdfium
into DIR/inputs/ (a maker's PDF is not redistributable, so DIR belongs outside git; CONTRACT §2).
A-vs-B agreement is the harness's text distance (normalised edit distance over the normalised content words) between
the two markdowns, and the order-free share of words only one of them has. WHY A runs twice: the harness starts a process per page, so its seconds include docling's model
load and CUDA warm-up; the second pass in the same process is the warm conversion time (CONTRACT §6: cold vs warm).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CACHE_URLS = {"lrs-350": "https://www.meanwell.com/Upload/PDF/LRS-350/LRS-350-SPEC.PDF",
              "tps54331": "https://www.ti.com/lit/ds/symlink/tps54331.pdf"}
PAGES = 4


def _cached_pdf(url: str) -> pathlib.Path:
    from mdify.cache import Cache

    root = Cache().root / "bundles"
    for record_path in root.glob("*/source.json"):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if any(retrieval.get("url") == url for retrieval in record.get("retrievals", [])):
            return record_path.parent / "source.pdf"
    raise FileNotFoundError(f"{url} is not in the markdownify cache; run `uv run mdify {url}` once")


def inputs(out: pathlib.Path) -> dict[str, pathlib.Path]:
    """The first PAGES pages of each file, written once into out/inputs/."""
    import pypdfium2

    directory = out / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in CACHE_URLS.items():
        path = directory / f"{name}.pdf"
        if not path.exists():
            original = pypdfium2.PdfDocument(str(_cached_pdf(url)))
            cut = pypdfium2.PdfDocument.new()
            cut.import_pages(original, list(range(min(PAGES, len(original)))))
            cut.save(str(path))
            cut.close()
            original.close()
        paths[name] = path
    return paths


def run(pipeline: str, model: str, out: pathlib.Path) -> None:
    from mdify import bundle, vlm
    from mdify.cache import Cache

    cache = Cache(out / "cache")
    for name, path in inputs(out).items():
        label = "a" if pipeline == "a" else f"{pipeline}-{model}"
        started = time.perf_counter()
        if pipeline == "a":
            built = bundle.build(str(path), out / label / name, cache=cache)
            warm = bundle.build(str(path), out / f"{label}-warm" / name, cache=cache)
            extra = {"warm": warm.kpis["time"]}
        else:
            built = vlm.build(str(path), out / label / name, backend=model, cache=cache,
                              place="words" if pipeline == "ab" else "chars")
            extra = {}
        record = {"pipeline": label, "file": name, "wall_seconds": round(time.perf_counter() - started, 3),
                  "kpis": built.kpis, **extra}
        (out / label / name / "real-run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        print(label, name, built.kpis["coverage"], built.kpis["time"])


def _words(directory: pathlib.Path) -> list[str]:
    from harness import mdtext

    return mdtext.normalized_words((directory / "document.md").read_text(encoding="utf-8"))


def _bag_difference(first: list[str], second: list[str]) -> float:
    """Share of words not common to both, ignoring order. WHY beside the edit distance: on a two-column page or a
    page of side-by-side tables the two pipelines read in different orders, which the edit distance counts as
    disagreement although both say the same words."""
    import collections

    a, b = collections.Counter(first), collections.Counter(second)
    differing = sum((a - b).values()) + sum((b - a).values())
    total = sum(a.values()) + sum(b.values())
    return round(differing / total, 4) if total else 0.0


def table(out: pathlib.Path, save: pathlib.Path | None) -> None:
    from rapidfuzz.distance import Levenshtein

    rows = []
    for record_path in sorted(out.glob("*/*/real-run.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        kpis = record["kpis"]
        directory = record_path.parent
        reference = out / "a" / record["file"]
        agreement = bag = None
        if record["pipeline"] != "a" and (reference / "document.md").exists():
            agreement = round(Levenshtein.normalized_distance(" ".join(_words(reference)),
                                                              " ".join(_words(directory))), 4)
            bag = _bag_difference(_words(reference), _words(directory))
        pages = kpis["time"]["pages"] or 1
        row = {"pipeline": record["pipeline"], "file": record["file"], "pages": kpis["time"]["pages"],
               "coverage": kpis["coverage"], "traceability": kpis["traceability"], "lost": kpis["silently_lost"],
               "unlinked": kpis["unlinked"], "inexact": kpis["inexact_words"],
               "markdown_words": len(json.loads((directory / "index.json").read_text())["words"]),
               "ned_vs_a": agreement, "bag_difference_vs_a": bag, "seconds_per_page": round(kpis["time"]["seconds"] / pages, 2)}
        if "warm" in record:
            row["warm_seconds_per_page"] = record["warm"]["seconds_per_page"]
        run_json = directory / "vlm" / "run.json"
        if run_json.exists():
            run_record = json.loads(run_json.read_text(encoding="utf-8"))
            row["llm_calls"] = len(run_record["calls"])
            row["placement"] = run_record.get("placement")
        rows.append(row)
    header = (f"{'pipeline':<10} {'file':<9} {'cover':>6} {'trace':>6} {'lost':>5} {'unlnk':>5} {'inexact':>7} "
              f"{'md words':>8} {'NED vs A':>8} {'bag vs A':>8} {'s/page':>7} {'warm s/p':>8}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['pipeline']:<10} {row['file']:<9} {row['coverage']!s:>6} {row['traceability']!s:>6} "
              f"{row['lost']!s:>5} {row['unlinked']:>5} {row['inexact']:>7} {row['markdown_words']:>8} "
              f"{row['ned_vs_a']!s:>8} {row['bag_difference_vs_a']!s:>8} {row['seconds_per_page']!s:>7} {row.get('warm_seconds_per_page', '-')!s:>8}")
    if save:
        save.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("run", "table"))
    parser.add_argument("pipeline", nargs="?", choices=("a", "b", "ab"))
    parser.add_argument("--model", choices=("claude", "codex"), default="claude")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--save", type=pathlib.Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        if not arguments.pipeline:
            parser.error("run takes a pipeline: a, b or ab")
        run(arguments.pipeline, arguments.model, arguments.out)
    else:
        table(arguments.out, arguments.save)
    return 0


if __name__ == "__main__":
    sys.exit(main())
