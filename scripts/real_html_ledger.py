"""Score mdify.html over robotparts' cached HTML pages: coverage, traceability, lost/unlinked, faithful check.
Run: uv run --directory /home/bam/markdownify python scripts/real_html_ledger.py [sha-prefix ...]
The real set (R0): the 46 web pages robotparts' ingestor cached on 2026-10-22."""
import bisect, glob, json, pathlib, sys, time, unicodedata
from mdify import html as mh
from mdify.align import markdown_words, normalize
from mdify.converters import Source

BLOBS = pathlib.Path.home() / ".cache/robotparts-ingest/blobs"
rows = []
flags = [a for a in sys.argv[1:] if a.startswith("-")]
sys.argv = [a for a in sys.argv if not a.startswith("-")]
for sidecar in sorted(BLOBS.glob("*.json")):
    meta = json.loads(sidecar.read_text())
    if "html" not in meta.get("media_type", ""):
        continue
    sha = meta["sha256"]
    if sys.argv[1:] and not any(sha.startswith(p) for p in sys.argv[1:]):
        continue
    url = meta["retrievals"][0]["url"]
    source = Source(BLOBS / sha, sha, "text/html", url)
    t0 = time.time(); conv = mh.convert(source); t1 = time.time(); inv = mh.inventory(source)
    ranges = sorted((w.grounding["html"]["source_range"]["start"], w.grounding["html"]["source_range"]["end"], k) for k, w in enumerate(inv))
    starts = [r[0] for r in ranges]
    md_text = {m.start: m.text for m in markdown_words(conv.markdown)}
    linked_src = set()
    unlinked = []; unfaithful = 0
    for w in conv.words:
        g = w["grounding"]
        if not g:
            unlinked.append(conv.markdown[w["range"]["start"]:w["range"]["end"]]); continue
        r = g["html"]["source_range"]
        k = bisect.bisect_right(starts, r["end"] - 1)
        j = k - 1
        while j >= 0 and ranges[j][1] > r["start"]:
            linked_src.add(ranges[j][2]); j -= 1
        under = normalize(mh.text_under(source, g))
        if w["score"] == 1.0 and under != normalize(md_text[w["range"]["start"]]):
            unfaithful += 1
            if "-v" in flags: print("UNFAITHFUL", repr(md_text[w["range"]["start"]]), repr(mh.text_under(source, g)))
    dropped_ranges = sorted((d["grounding"]["html"]["source_range"]["start"], d["grounding"]["html"]["source_range"]["end"], d["reason"]) for d in conv.dropped)
    dstarts = [d[0] for d in dropped_ranges]
    dropped = {}; lost = []
    for k, w in enumerate(inv):
        if k in linked_src: continue
        r = w.grounding["html"]["source_range"]
        i = bisect.bisect_right(dstarts, r["start"]) - 1
        if i >= 0 and dropped_ranges[i][0] <= r["start"] and r["end"] <= dropped_ranges[i][1]:
            dropped[dropped_ranges[i][2]] = dropped.get(dropped_ranges[i][2], 0) + 1
        else:
            lost.append(w.text)
    n_src = len(inv); n_drop = sum(dropped.values()); n_md = len(conv.words); n_link = n_md - len(unlinked)
    cov = len(linked_src) / (n_src - n_drop) if n_src - n_drop else 1.0
    tr = n_link / n_md if n_md else 1.0
    rows.append(dict(sha=sha[:10], url=url, src=n_src, linked_src=len(linked_src), dropped=dropped, lost=len(lost), md=n_md,
                     unlinked=len(unlinked), coverage=round(cov, 4), traceability=round(tr, 4), unfaithful=unfaithful,
                     seconds=round(t1 - t0, 2), lost_sample=" ".join(lost[:25]), unlinked_sample=" | ".join(unlinked[:15])))
    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
tot = lambda k: sum(r[k] for r in rows)
src = tot("src"); drop = sum(sum(r["dropped"].values()) for r in rows)
print(json.dumps({"pages": len(rows), "source_words": src, "linked_source": tot("linked_src"), "dropped": drop,
                  "lost": tot("lost"), "md_words": tot("md"), "unlinked": tot("unlinked"), "unfaithful": tot("unfaithful"),
                  "coverage": round(tot("linked_src") / (src - drop), 4), "traceability": round(1 - tot("unlinked") / tot("md"), 4)}))
