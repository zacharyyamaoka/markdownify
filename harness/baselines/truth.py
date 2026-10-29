"""Baseline "truth": hands back the truth bundle itself, so the ruler must score it perfect.

    uv run python harness/baselines/truth.py <truth bundle dir> <bundle out dir>

WHY it exists: a scorer that cannot give a perfect answer a perfect score is measuring itself, not the
pipeline. Only the bundle files of CONTRACT §2 are copied — never truth.json, which the scorer reads from the
truth side.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

BUNDLE_FILES = ("source.json", "document.md", "index.json", "ledger.json", "kpis.json")


def main(argv: list[str]) -> int:
    truth_dir, bundle_dir = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for path in truth_dir.iterdir():
        if path.name in BUNDLE_FILES or path.name.startswith("source."):
            shutil.copyfile(path, bundle_dir / path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
