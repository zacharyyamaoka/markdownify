"""Keep the harness's pipeline A vs pipeline B runs and print them side by side.

    uv run python scripts/pipeline_b_results.py save vlm-claude claude      # results.json key → <label>-<arm>.json
    uv run python scripts/pipeline_b_results.py table                        # every saved file, one row per file

WHY a copy: harness/run.py keeps one entry per pipeline name in harness/out/results.json (gitignored), and the next
run of that name overwrites it. The saved files, one per pipeline × arm, are what the comparison report cites.
Totals are summed over cases (word counts) or averaged over the cases that have them (TEDS, header paths).
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESULTS = REPO / "harness" / "out" / "results.json"
SAVED = REPO / "scripts" / "pipeline_b_results"


def save(key: str, label: str) -> None:
    entry = json.loads(RESULTS.read_text(encoding="utf-8"))[key]
    SAVED.mkdir(parents=True, exist_ok=True)
    for arm in entry["arms"]:
        cases = {case: arms[arm] for case, arms in entry["cases"].items() if arm in arms}
        path = SAVED / f"{label}-{arm}.json"
        path.write_text(json.dumps({"key": key, "command": entry["command"], "arm": arm, "ran_at": entry["ran_at"],
                                    "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(path.relative_to(REPO))


def _mean(values: list) -> float | None:
    values = [value for value in values if value is not None]
    return round(statistics.mean(values), 3) if values else None


def totals(saved: dict) -> dict:
    records = list(saved["cases"].values())
    scored = [record["scores"] for record in records if "scores" in record]
    location = {name: sum(score["location"][name] for score in scored)
                for name in ("correct", "misplaced", "unlinked", "ambiguous")}
    truth_words = sum(score["truth_words"] for score in scored)
    return {"cases": len(records), "failed": len(records) - len(scored),
            "ned": _mean([score["ned"] for score in scored]),
            "teds": _mean([score["teds_mean"] for score in scored]),
            "header_paths": _mean([score["header_path_accuracy"] for score in scored]),
            **location, "truth_words": truth_words,
            "correct_rate": round(location["correct"] / truth_words, 3) if truth_words else None,
            "lost": sum(score["ledger"]["lost"] for score in scored),
            "md_unlinked": sum(score["ledger"]["unlinked"] for score in scored),
            "seconds_per_page": _mean([record["seconds"] for record in records])}


def table() -> None:
    header = (f"{'run':<14} {'cases':>5} {'fail':>4} {'NED':>6} {'TEDS':>6} {'hdr':>6} {'correct':>12} "
              f"{'mispl':>5} {'unlnk':>5} {'amb':>4} {'lost':>4} {'md-unl':>6} {'s/page':>7}")
    print(header)
    print("-" * len(header))
    for path in sorted(SAVED.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if "cases" not in saved:
            continue   # coordinates-test.json: the one-case box-format test, not a run
        row = totals(saved)
        correct = f"{row['correct']}/{row['truth_words']} {row['correct_rate']}"
        print(f"{path.stem:<14} {row['cases']:>5} {row['failed']:>4} {row['ned']!s:>6} {row['teds']!s:>6} "
              f"{row['header_paths']!s:>6} {correct:>12} {row['misplaced']:>5} {row['unlinked']:>5} "
              f"{row['ambiguous']:>4} {row['lost']:>4} {row['md_unlinked']:>6} {row['seconds_per_page']!s:>7}")


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "save":
        save(argv[1], argv[2])
        return 0
    if argv == ["table"]:
        table()
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
