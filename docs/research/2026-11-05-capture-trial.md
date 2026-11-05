# Capture trial: `mdify capture` vs SingleFile vs Scoop (2026-11-05)

**Verdict.** SingleFile is adopted as a new capture level, `single`. Scoop is not adopted: our recorder stays. ReplayWeb.page
is vendored behind one "Full archive" button in `mdify view`.

- **SingleFile** writes one HTML file that renders offline with no network request at all. Its copy is a second
  visit, not the same instant, so its pixel difference from our snapshot is visit-to-visit change (a video gallery's
  state, a timed modal), not lost content. It is a display copy. The converter can't read it on image-heavy pages
  (see "What the converter makes of a SingleFile copy").
- **Scoop** records more responses than we do on CubeMars (238 vs 198). It replays CubeMars a little worse (0.13 % of
  pixels changed vs 0.01 %). Its eRob80 capture was partial: the page load hit Scoop's default 20 s limit, and it
  recorded 71 responses to our 118. It doesn't read robots.txt or pace requests. Before the browser opens it sends
  one extra request for the page, outside the browser. It saves twice the bytes on CubeMars. And it can't measure
  word boxes: it has no hook into its page. That misses the bar we set, "at least as good on completeness and
  replay, keeping our politeness gate".
- **ReplayWeb.page** replays both our archives and Scoop's offline with zero requests off the machine. On CubeMars
  that was verified through the real `mdify view` button.

Everything below was measured on 2026-11-05 between 20:03 and 20:21 UTC. Each tool visited each page once (after
`Fetcher.permit`: robots.txt, then one request per second per host). Everything after that ran offline. The harness is
`tools/scoop/trial.py` + `tools/scoop/rwp_replay.py`. The raw results (the captures, the replay screenshots, the
JSON per stage) are outside git, in `~/.cache/markdownify/trial-2026-11-05/`.

## Pages

- CubeMars AK80-9: `https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html` (a shop page: video gallery,
  a timed sign-up modal, cookie banner, trackers)
- ZeroErr eRob80: `https://www.zeroerr.cn/eRob/eRob80.html` (navigates itself to `?t=<now>` after load)
- moteus hardware reference: `https://mjbots.github.io/moteus/reference/hardware/` (a static MkDocs page)

## The comparison

Pixel difference is the share of pixels with any channel off by more than 40 (`mdify.capture_replay.compare`),
measured against our `snapshot.jpg` (1280 px wide, full page) of the same day.

| page | tool | size | time | offline replay vs snapshot | completeness |
|---|---|---|---|---|---|
| CubeMars | **ours** (`archive`) | 6.1 MB | 23.0 s * | **0.01 %** (same height) | 198 responses (193 URLs); 4 bodies the browser no longer had |
| CubeMars | SingleFile (`single`) | 3.6 MB | 71.5 s † | 15.4 % (16 130 vs 16 208 px tall) | one file; 0 requests when opened offline |
| CubeMars | Scoop | 13.3 MB (5.3 MB is its PNG screenshot) | 32.0 s | 0.13 % (same height) | **238 responses (208 URLs)** |
| eRob80 | **ours** | 7.3 MB | 31.1 s * | **0.00 %** | **118 responses (73 URLs)** |
| eRob80 | SingleFile | 7.6 MB | 34.6 s | 6.7 % (11 555 vs 11 540 px) | one file; 0 requests offline |
| eRob80 | Scoop | 8.5 MB (2.8 MB screenshot) | 62.6 s | blank in our replayer ‡; 0.34 % in ReplayWeb.page | 71 responses (63 URLs), **partial**: load timed out at 20 s |
| moteus | **ours** | 285 KB | 6.1 s * | 0.00 % | 18 responses |
| moteus | SingleFile | 160 KB | 7.7 s | 0.00 % | one file; 0 requests offline |
| moteus | Scoop | 424 KB (128 KB screenshot) | 2.2 s | 0.00 % | 19 responses |

\* Ours is the whole `mdify capture` with every level. That is the visit, the snapshot with word boxes, the PDF, the
WACZ and its verification, plus the conversion (3.6 / 1.4 / 1.0 s of the total).
† SingleFile waits for network idle, which never came on CubeMars (trackers poll), so it waited its 60 s limit.
‡ Our replayer (`capture_replay`) answers only exact URLs, with the capture's clock. Scoop's page `ts` is not when
the page computed its `?t=`, so the re-navigation asked for a URL the archive doesn't hold. ReplayWeb.page matches
`?t=` loosely and replays it.

**Our other levels, same visit:** CubeMars html 167 KB, snapshot 1.5 MB, pdf 5.2 MB. eRob80 84 KB, 1.2 MB, 6.9 MB.
moteus 45 KB, 164 KB, 95 KB. These are within a few percent of the 2026-10-29 figures in the README.

### Replay through ReplayWeb.page (the vendored 2.5.4, the same for every archive)

Every archive replayed with **0 requests off 127.0.0.1**. The page shows its full-height screenshots by stretching
the replay frame to the page's height, the way a full-page screenshot stretches the viewport.

| page | ours | Scoop |
|---|---|---|
| moteus | 0.35 % | 0.35 % |
| eRob80 | 0.25 % | 0.34 % |
| CubeMars | 47.8 % full page, 64.5 % first screen | 47.8 % full page, 63.5 % first screen |

On CubeMars the two archives come out the same, so the number measures the replay method, not the archive. The
replayed page renders with every image (133 loaded). Our snapshot has the timed "$10 Register Now" modal and its
dark overlay over the whole page; the replay screenshot is taken before the modal's timer fires. In the "Full
archive" tab, a few seconds later, the modal is there as captured (`shots/archive-replay-cubemars.png` in the trial results).

### SingleFile offline, by eye

- **moteus:** pixel-identical to the snapshot.
- **CubeMars:** layout and text match. Differences:
  - the video gallery shows its player controls (SingleFile removes scripts, so the gallery stays in its first state);
  - the product panel sits 78 px higher;
  - the sign-up modal and the cookie banner are kept, as rendered.
- **eRob80:** identical down to y = 3 823 px. Below that, a section has a different height.

## What the converter makes of a SingleFile copy (`mdify <single.html>`, then `mdify check`)

- **moteus:** PASS on all five invariants. It links 234 words, the same 234 that `source.html` links.
- **CubeMars and eRob80:** the conversion didn't finish. CubeMars was stopped after 15 min, eRob80 after 12 min.
  - **Root cause:** SingleFile inlines every image as a `data:` URI (96 images, 2.75 MB on CubeMars; 42, 4.2 MB on
    eRob80). Defuddle's markdown carries them as image URLs.
  - **Where the time goes:** `html._range_mapper` runs `difflib.SequenceMatcher(autojunk=False)` over that markdown,
    which is quadratic (stack sampled with faulthandler at 120 s).
- **Consequence:** `single.html` is a display copy only. Values keep citing `source.html` (CONTRACT §2.1).
- **If the converter should ever read it:** strip `data:` URLs from the markdown before the diff. That is in
  `html.py`, outside this change.
- **Baseline for comparison:** our own eRob80 bundle fails `mdify check` invariant 4 too (a table row spills into a
  third column). That failure was there before this change.

## Politeness

| | ours | SingleFile (`single`) | Scoop |
|---|---|---|---|
| robots.txt on the page URL | yes | yes (`Fetcher.permit` before it) | no (we could wrap it with `permit`) |
| one request/s/host on the page URL | yes | yes (`permit`) | no (`permit` could wrap it) |
| robots.txt on redirects and frames inside the visit | yes | no | no |
| refused hosts (McMaster, Octopart) blocked inside the page | yes (route) | yes (`--blocked-url-pattern`) | possible (its `blocklist` takes regexes) |
| extra requests | none | its own second visit; at most 6 parallel per host | one request for the page outside the browser before the visit; behaviours fetch extra `srcset` images |
| user agent | the real Chromium UA + `mdify-capture/0.1 (+https://robotparts.dev/)` | the same string | a Windows Chrome UA + our suffix |
| SSRF guard | refused hosts only | none | private-IP blocklist |

## Code Scoop would replace

The prior-art note counted 675 lines: `capture_browser.py` (453) + `capture_archive.py` (222).

- **What Scoop could take over:** the recorder (~140 lines: the route handler, the redirect fallback, `Exchange`,
  the main-document pick) and the WARC/WACZ writer (~220). That is about 360 lines.
- **What stays:** the word walk (153 lines of JS), the scroll, the full-page JPEG and the PDF. Scoop's library has
  no hook into its Playwright page, so they would run in a second browser session, on the replay.
- **What it costs:** that second render per capture, a second Chromium build (Scoop pins Playwright 1.63 / Chromium
  1243), 74 npm packages, and a postinstall that downloads yt-dlp and crip (denied in `tools/scoop/pnpm-workspace.yaml`).
- **What it loses:** our robots.txt check on in-visit navigations, and bodies stored decoded with a hex sha256 digest
  (the archive check finds the main document by the bundle's own sha256). Scoop stores bodies as sent (gzip/br): that
  is more literal, but the main document's digest is then not `source.html`'s sha256.

**When to try again:** a Scoop release with a page hook, or with robots.txt support. Re-run the trial with
`--load-timeout 60000`, because the eRob80 shortfall may be its 20 s default and not the recorder. That would be one
more visit, so it was not done today.

## How to re-run

```
pnpm --dir /home/bam/markdownify/tools/scoop install
uv run --project /home/bam/markdownify python -I /home/bam/markdownify/tools/scoop/trial.py ours
```

The stages are `ours`, `single`, `scoop` (these visit the site), then `offline_single`, `check_single`,
`check_ours` and `replay_scoop` (these are offline). `rwp_replay.py` runs the ReplayWeb.page replay.
