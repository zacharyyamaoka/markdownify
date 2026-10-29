"""Why a pipeline B word is misplaced: the model's line box, or our split of it by character count?

    uv run python scripts/pipeline_b_diagnose.py vlm-claude [--crops reports/media/pipeline-b]

For every truth word the harness pairs with a B word (harness.score's own alignment, imported, never edited), the
B word's line is the union of the B words that share its line box (same page, y0 and y1 — the split keeps a line's
y). A misplaced word whose truth box still lies inside that line (IoA ≥ 0.5) was placed by our character-count
split, not by the model: "split". One whose truth box is outside it is the model's own line box: "line".
With --crops, the worst case's page is cropped around each misplaced word: truth box green, B's word box red,
B's line box blue.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness import mdtext, score


def diagnose(truth_dir: pathlib.Path, candidate_dir: pathlib.Path) -> dict:
    truth_document, truth_index = score.load_bundle(truth_dir)
    candidate_document, candidate_index = score.load_bundle(candidate_dir)
    truth_words = mdtext.words(truth_document)
    candidate_words = mdtext.words(candidate_document)
    _, _, pairs = score._aligned_words(truth_words, candidate_words)
    links = score._candidate_links(candidate_words, candidate_index)
    lines: dict[tuple, list] = {}
    for word in candidate_index["words"]:
        grounding = word.get("grounding")
        if grounding:
            box = grounding["box"]
            lines.setdefault((grounding["page"], box[1], box[3]), []).append(box)
    counts = {"correct": 0, "split": 0, "line": 0, "other": 0}
    misplaced = []
    for truth_position, truth_row in enumerate(truth_index["words"]):
        candidate_position = pairs.get(truth_position)
        shapes = links[candidate_position] if candidate_position is not None else []
        if not shapes:
            counts["other"] += 1
            continue
        truth_shape = score._box(truth_row["grounding"])
        shape = score._union(shapes)
        if score.localised(truth_shape, shape):
            counts["correct"] += 1
            continue
        page, box = shape[1], shape[2]
        line_boxes = lines.get((page, box[1], box[3]), [list(box)])
        line = ("box", page, (min(b[0] for b in line_boxes), min(b[1] for b in line_boxes),
                              max(b[2] for b in line_boxes), max(b[3] for b in line_boxes)))
        cause = "split" if score.ioa(truth_shape, line) >= 0.5 else "line"
        counts[cause] += 1
        misplaced.append({"word": truth_words[truth_position].text, "cause": cause, "truth": truth_shape[2],
                          "candidate": box, "line": line[2]})
    return {"counts": counts, "misplaced": misplaced}


def crop(page_png: pathlib.Path, misplaced: list[dict], out: pathlib.Path) -> None:
    from PIL import Image, ImageDraw

    with Image.open(page_png) as picture:
        picture = picture.convert("RGB")
        draw = ImageDraw.Draw(picture)
        width, height = picture.size

        def pixels(box):
            return [box[0] * width, box[1] * height, box[2] * width, box[3] * height]

        for item in misplaced:
            draw.rectangle(pixels(item["line"]), outline=(40, 90, 230), width=2)
            draw.rectangle(pixels(item["truth"]), outline=(20, 160, 60), width=3)
            draw.rectangle(pixels(item["candidate"]), outline=(220, 30, 30), width=2)
        top = max(min(item["line"][1] for item in misplaced) * height - 40, 0)
        bottom = min(max(item["line"][3] for item in misplaced) * height + 40, height)
        picture.crop((0, top, width, bottom)).save(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", help="harness/out/runs/<run>")
    parser.add_argument("--crops", type=pathlib.Path, help="write a crop per case with misplaced words here")
    arguments = parser.parse_args(argv)
    total = {"correct": 0, "split": 0, "line": 0, "other": 0}
    for case_dir in sorted((REPO / "harness" / "out" / "runs" / arguments.run).iterdir()):
        for arm_dir in sorted(path for path in case_dir.iterdir() if (path / "bundle" / "index.json").exists()):
            result = diagnose(REPO / "harness" / "out" / case_dir.name / arm_dir.name, arm_dir / "bundle")
            for name, value in result["counts"].items():
                total[name] += value
            print(f"{case_dir.name:<22} {arm_dir.name:<4} {result['counts']}")
            if arguments.crops and result["misplaced"]:
                arguments.crops.mkdir(parents=True, exist_ok=True)
                out = arguments.crops / f"{arguments.run}-{case_dir.name}-{arm_dir.name}.png"
                crop(arm_dir / "bundle" / "vlm" / "page-1.png", result["misplaced"], out)
    print(f"{'total':<27} {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
