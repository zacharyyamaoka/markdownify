"""Baseline "pdftotext": poppler's `pdftotext -layout` output as the markdown, with no groundings at all.

    uv run python harness/baselines/pdftotext.py <source.pdf> <bundle out dir>

WHY: the floor every pipeline should beat. Its text is scored like any other; with no index words, its location
is all unlinked and its ledger all lost — which is what "no source map" means. PDF only: it exits 2 for the png
and html arms rather than pretend.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys


def main(argv: list[str]) -> int:
    source, bundle_dir = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    if source.suffix.lower() != ".pdf":
        print(f"pdftotext reads PDFs only, not {source.name}", file=sys.stderr)
        return 2
    bundle_dir.mkdir(parents=True, exist_ok=True)
    text = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(source), "-"],
                          capture_output=True, text=True, check=True).stdout
    text = text.replace("\f", "\n")
    data = source.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    shutil.copyfile(source, bundle_dir / "source.pdf")
    (bundle_dir / "document.md").write_text(text, encoding="utf-8", newline="\n")
    (bundle_dir / "source.json").write_text(json.dumps(
        {"sha256": sha256, "bytes": len(data), "media_type": "application/pdf", "retrievals": []}, indent=1) + "\n")
    (bundle_dir / "index.json").write_text(json.dumps({
        "schema": "markdownify/index@0",
        "tool": {"name": "pdftotext", "version": "poppler", "pipeline": "baseline"},
        "source": {"sha256": sha256, "media_type": "application/pdf"},
        "markdown_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "range_units": "unicode_codepoints", "pages": [], "blocks": [], "words": [], "dropped": []}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
