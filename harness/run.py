"""Run a pipeline over every case × arm, time it, score it, and keep the results.

    uv run python harness/run.py --pipeline 'uv run mdify {source} --out {bundle}'
    uv run python harness/run.py --pipeline 'uv run python harness/baselines/truth.py {truth} {bundle}'
    uv run python harness/run.py --pipeline 'uv run python harness/baselines/pdftotext.py {source} {bundle}' --arms pdf

Placeholders: {source} is the case's source file, copied into a fresh `input/` directory first. WHY a copy: the
truth bundle sits next to the rendered source, and a pipeline under test must not be able to read it. {bundle} is
the empty directory the pipeline writes its bundle into. {truth} is the truth bundle itself — for the truth
baseline only, which exists to prove the ruler scores a perfect answer as perfect.

Cases are rendered first if harness/out lacks them. Bundles go to harness/out/runs/<name>/<case>/<arm>/bundle;
scores beside them; the summary of every pipeline run so far in harness/out/results.json (one key per pipeline
name, so baselines and mdify sit side by side). Arms are scored and printed separately, never pooled.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import shlex
import shutil
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from harness import render, score

ARMS = ("pdf", "png", "html")
SOURCE_NAMES = {"pdf": "source.pdf", "png": "source.png", "html": "source.html"}
REPO = pathlib.Path(__file__).resolve().parent.parent


def pipeline_name(template: str) -> str:
    """A short name from the command: the first word that is not a launcher ("uv run python x/truth.py" → truth)."""
    for token in shlex.split(template):
        if token in ("uv", "run", "python", "python3") or token.startswith("-") or token.startswith("{"):
            continue
        return pathlib.Path(token).stem
    return "pipeline"


def summary(scores: dict) -> dict:
    """The few numbers results.json keeps per case × arm (scores.json beside the bundle has everything)."""
    text, tables, location, ledger = scores["text"], scores["tables"], scores["location"], scores["ledger"]
    return {"ned": text["ned"], "substitutions": text["substitutions"], "deletions": text["deletions"],
            "insertions": text["insertions"], "truth_words": text["truth_words"],
            "teds_mean": tables.get("teds_mean"), "header_path_accuracy": tables.get("header_path_accuracy"),
            "location": {name: location[name] for name in ("correct", "misplaced", "unlinked", "ambiguous")},
            "location_correct_rate": location["correct_rate"], "median_iou": location["median_iou"],
            "ledger": {name: ledger[name] for name in ("linked", "lost", "unlinked", "dropped", "flagged",
                                                       "wrong_drops", "intended_drops_kept", "intended_drops_lost")},
            "ladder": scores["ladder"]}


def run_one(template: str, truth_dir: pathlib.Path, run_dir: pathlib.Path, arm: str, timeout: float) -> dict:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    input_dir, bundle_dir = run_dir / "input", run_dir / "bundle"
    input_dir.mkdir(parents=True)
    bundle_dir.mkdir()
    source = input_dir / SOURCE_NAMES[arm]
    shutil.copyfile(truth_dir / SOURCE_NAMES[arm], source)
    command = template.format(source=shlex.quote(str(source)), bundle=shlex.quote(str(bundle_dir)),
                              truth=shlex.quote(str(truth_dir)))
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, shell=True, cwd=REPO, capture_output=True, text=True, timeout=timeout)
        returncode, stderr = completed.returncode, completed.stderr
    except subprocess.TimeoutExpired as expired:
        returncode, stderr = None, f"timed out after {timeout:.0f} s\n{expired.stderr or ''}"
    seconds = round(time.perf_counter() - started, 3)
    (run_dir / "stderr.txt").write_text(stderr if isinstance(stderr, str) else stderr.decode(errors="replace"))
    record = {"command": command, "seconds": seconds, "returncode": returncode}
    if returncode != 0 or not (bundle_dir / "document.md").exists():
        tail = (stderr if isinstance(stderr, str) else "").strip().splitlines()[-3:]
        record["error"] = " | ".join(tail) or f"exit {returncode}, no document.md"
        return record
    scores = score.score(truth_dir, bundle_dir)
    (run_dir / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    record["scores"] = summary(scores)
    return record


def format_table(cases: dict) -> str:
    header = (f"{'case':<22} {'arm':<4} {'sec':>6}  {'NED':>6} {'S/D/I':>11}  {'TEDS':>5} {'hdr':>5}  "
              f"{'loc ok':>6} {'mispl':>5} {'unlnk':>5}  {'lost':>4} {'md-unl':>6} {'drops':>5}")
    lines = [header, "-" * len(header)]
    for case_id, arms in cases.items():
        for arm, record in arms.items():
            if "scores" not in record:
                lines.append(f"{case_id:<22} {arm:<4} {record['seconds']:>6.2f}  FAILED: {record['error'][:70]}")
                continue
            s = record["scores"]
            teds = "-" if s["teds_mean"] is None else f"{s['teds_mean']:.3f}"
            header_paths = "-" if s["header_path_accuracy"] is None else f"{s['header_path_accuracy']:.3f}"
            correct = "-" if s["location_correct_rate"] is None else f"{s['location_correct_rate']:.3f}"
            drops = sum(s["ledger"]["dropped"].values())
            lines.append(
                f"{case_id:<22} {arm:<4} {record['seconds']:>6.2f}  {s['ned']:>6.3f} "
                f"{s['substitutions']:>3}/{s['deletions']:>3}/{s['insertions']:>3}  {teds:>5} {header_paths:>5}  "
                f"{correct:>6} {s['location']['misplaced']:>5} {s['location']['unlinked']:>5}  "
                f"{s['ledger']['lost']:>4} {s['ledger']['unlinked']:>6} {drops:>5}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pipeline", required=True, help="command template with {source} and {bundle}")
    parser.add_argument("--name", help="results key (default: from the command)")
    parser.add_argument("--arms", default=",".join(ARMS), help="comma-separated subset of pdf,png,html")
    parser.add_argument("--cases", default="", help="comma-separated case names or number prefixes (default all)")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per case × arm")
    parser.add_argument("--out", type=pathlib.Path, default=render.OUT_DIR)
    arguments = parser.parse_args(argv)

    arms = [arm.strip() for arm in arguments.arms.split(",") if arm.strip()]
    if unknown := set(arms) - set(ARMS):
        parser.error(f"unknown arms {sorted(unknown)}")
    names = [name.strip() for name in arguments.cases.split(",") if name.strip()]
    case_dirs = render.case_dirs(names, all_cases=not names)
    missing = [d.name for d in case_dirs if not all((arguments.out / d.name / arm / "truth.json").exists()
                                                   for arm in ARMS)]
    if missing:
        print(f"rendering {len(missing)} case(s) first: {', '.join(missing)}", file=sys.stderr)
        rendered = render.render(missing, out_root=arguments.out)
        failed = [case for case, result in rendered.items() if not result.get("passed")]
        if failed:
            print(f"cases failed their self-check and are skipped: {failed}", file=sys.stderr)
            case_dirs = [d for d in case_dirs if d.name not in failed]

    name = arguments.name or pipeline_name(arguments.pipeline)
    cases: dict[str, dict] = {}
    for case_dir in case_dirs:
        for arm in arms:
            truth_dir = arguments.out / case_dir.name / arm
            run_dir = arguments.out / "runs" / name / case_dir.name / arm
            cases.setdefault(case_dir.name, {})[arm] = run_one(arguments.pipeline, truth_dir, run_dir, arm,
                                                               arguments.timeout)

    results_path = arguments.out / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    results[name] = {"command": arguments.pipeline, "arms": arms,
                     "ran_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "cases": cases}
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"pipeline {name}: {arguments.pipeline}")
    print(format_table(cases))
    print(f"\nresults: {results_path}")
    return 0 if all("scores" in record for arms_ in cases.values() for record in arms_.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
