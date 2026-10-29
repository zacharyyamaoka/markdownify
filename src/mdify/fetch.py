"""Polite HTTP for Markdownify: robots.txt first, at most one request per second per host, and a few hosts it never
touches. Every response it keeps goes into the cache by sha256 before anything reads it.

Adapted from robotparts' `tools/ingestor/fetch.py` (same author); the rules are unchanged. WHY these rules (Zach's
research note "Viam and Full Stack Component Prior Art for robotparts", 2026-10-22, and that lane's brief):
- robots.txt is honoured for our user agent, and a robots.txt that answers 401/403 or 5xx means "no";
- one request per second per host at most, raised to the site's own Crawl-delay when it asks for more,
  enforced across processes through a per-host lock in the cache;
- McMaster-Carr is never fetched (no scraping at all), and neither is Octopart/Nexar, whose terms forbid
  storing their data beyond 24 hours (this cache keeps every input for good);
- redirects are followed by hand, so each hop is checked against its own host's robots.txt and pace.

What changed from robotparts: the media types (Markdownify also reads PNG/JPEG page images), the user agent, and the
retrieval record's `http_status` field name (CONTRACT §2).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

from . import __version__
from .cache import Cache, iso_z, sha256_bytes, utc_now

USER_AGENT = f"mdify/{__version__} (Markdownify; reads makers' datasheets for an open parts library, +https://robotparts.dev/)"
ROBOTS_AGENT = "mdify"
MIN_INTERVAL_S = 1.0
TIMEOUT_S = 60.0
MAX_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5
ROBOTS_MAX_AGE_S = 24 * 3600
REFUSED_HOSTS = {
    "mcmaster.com": "McMaster-Carr is never scraped (robotparts research note, 2026-10-22)",
    "octopart.com": "Octopart/Nexar data may not be stored beyond 24 h (nexar.com/api/legal); this cache keeps every input",
    "nexar.com": "Octopart/Nexar data may not be stored beyond 24 h (nexar.com/api/legal); this cache keeps every input",
}
CITABLE_MEDIA_TYPES = ("application/pdf", "text/html", "image/png", "image/jpeg")


class FetchRefused(Exception):
    """The fetcher will not request this URL (a refused host, or robots.txt says no)."""


class FetchError(Exception):
    """The request was made and failed (HTTP error, too large, not a media type Markdownify reads)."""


@dataclasses.dataclass
class Fetched:
    url: str              # the URL that was asked for (what a citation cites)
    final_url: str        # where the bytes came from after redirects
    sha256: str
    media_type: str
    retrieved_at: str     # UTC, YYYY-MM-DDTHH:MM:SSZ
    status: int | None
    from_cache: bool


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def normalize_url(url: str) -> str:
    """Percent-encode what a URL may not carry raw (spaces, non-ASCII), keeping existing escapes, so the URL
    that is fetched is the URL a citation cites."""
    parts = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%:@!$'()*+,;/?-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def refused_reason(url: str) -> str | None:
    host = host_of(url)
    for domain, reason in REFUSED_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None


def media_type_of(content_type: str | None, data: bytes) -> str:
    """The media type to record: sniffed from the bytes first (a PDF served as octet-stream is a PDF)."""
    head = data[:1024]
    if b"%PDF-" in head:
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ("text/html", "application/xhtml+xml"):
        return "text/html"
    lowered = data[:4096].lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "text/html"
    return declared or "application/octet-stream"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib's signature
        return None


class Fetcher:
    def __init__(self, cache: Cache, *, user_agent: str = USER_AGENT, min_interval: float = MIN_INTERVAL_S,
                 timeout: float = TIMEOUT_S, clock=time.time, sleep=time.sleep, log=None):
        self.cache = cache
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.log = log or (lambda message: None)
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}

    # --- public ------------------------------------------------------------------------------------
    def fetch(self, url: str, *, refresh: bool = False) -> Fetched:
        """The bytes of `url`, from the cache when it already holds a retrieval of it (unless `refresh`)."""
        if not url.startswith(("http://", "https://")):
            raise FetchRefused(f"{url!r} is not an http(s) URL")
        url = normalize_url(url)
        reason = refused_reason(url)
        if reason:
            raise FetchRefused(f"{host_of(url)}: {reason}")
        if not refresh:
            cached = self.cache.latest_retrieval(url)
            if cached is not None and self.cache.has(cached[0]):
                sha256, retrieval = cached
                meta = self.cache.meta(sha256) or {}
                return Fetched(url=url, final_url=retrieval.get("final_url") or url, sha256=sha256,
                               media_type=meta.get("media_type", "application/octet-stream"),
                               retrieved_at=retrieval["retrieved_at"], status=retrieval.get("http_status"),
                               from_cache=True)
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            reason = refused_reason(current)
            if reason:
                raise FetchRefused(f"{host_of(current)}: {reason} (redirected from {url})")
            allowed, why = self.allowed(current)
            if not allowed:
                raise FetchRefused(why)
            status, headers, body = self._request(current, MAX_BYTES)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("Location")
                if not location:
                    raise FetchError(f"{current}: HTTP {status} without a Location")
                current = normalize_url(urllib.parse.urljoin(current, location))
                continue
            if status != 200:
                raise FetchError(f"{current}: HTTP {status}")
            media_type = media_type_of(headers.get("Content-Type"), body)
            if media_type not in CITABLE_MEDIA_TYPES:
                raise FetchError(f"{current}: {media_type} is not a source Markdownify reads ({CITABLE_MEDIA_TYPES})")
            retrieved_at = iso_z(utc_now())
            sha256 = self.cache.put(body, media_type, {
                "url": url, "final_url": current, "retrieved_at": retrieved_at, "http_status": status,
                "content_type": headers.get("Content-Type"), "user_agent": self.user_agent})
            return Fetched(url=url, final_url=current, sha256=sha256, media_type=media_type,
                           retrieved_at=retrieved_at, status=status, from_cache=False)
        raise FetchError(f"{url}: more than {MAX_REDIRECTS} redirects")

    def permit(self, url: str) -> str:
        """The same gate `fetch` puts in front of every request — a refused host, then robots.txt — and this host's
        pace, for a request someone else makes: `mdify capture`'s browser opens the page itself. Returns the
        normalised URL; raises FetchRefused."""
        if not url.startswith(("http://", "https://")):
            raise FetchRefused(f"{url!r} is not an http(s) URL")
        url = normalize_url(url)
        reason = refused_reason(url)
        if reason:
            raise FetchRefused(f"{host_of(url)}: {reason}")
        allowed, why = self.allowed(url)
        if not allowed:
            raise FetchRefused(why)
        parsed = urllib.parse.urlparse(url)
        self._turn(parsed.netloc.lower(), self._robots_for(f"{parsed.scheme}://{parsed.netloc}")[1])
        return url

    def allowed(self, url: str) -> tuple[bool, str]:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser, _delay = self._robots_for(origin)
        if parser.can_fetch(ROBOTS_AGENT, url):
            return True, "allowed by robots.txt"
        return False, f"{origin}/robots.txt disallows {url} for {ROBOTS_AGENT}"

    # --- robots.txt ----------------------------------------------------------------------------------
    def _robots_for(self, origin: str) -> tuple[urllib.robotparser.RobotFileParser, float]:
        if origin in self._robots:
            return self._robots[origin]
        host = urllib.parse.urlparse(origin).netloc.lower()
        record_path = self.cache.root / "robots" / f"{host.replace(':', '_')}.json"
        record = None
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if self.clock() - record.get("fetched_at_epoch", 0) > ROBOTS_MAX_AGE_S:
                record = None
        if record is None:
            record = self._read_robots(origin)
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        parser = urllib.robotparser.RobotFileParser()
        status = record.get("status")
        if status in (401, 403) or status is None or status >= 500:
            # WHY: RFC 9309 — an unreachable or forbidden robots.txt means the site has not allowed us in.
            parser.disallow_all = True
        elif 400 <= status < 500:
            parser.allow_all = True
        else:
            parser.parse(record.get("text", "").splitlines())
        parser.modified()
        delay = parser.crawl_delay(ROBOTS_AGENT)
        rate = parser.request_rate(ROBOTS_AGENT)
        pace = max(self.min_interval, float(delay or 0), (rate.seconds / rate.requests) if rate else 0.0)
        self._robots[origin] = (parser, pace)
        return parser, pace

    def _read_robots(self, origin: str) -> dict:
        url = origin + "/robots.txt"
        current = url
        record = {"url": url, "fetched_at": iso_z(utc_now()), "fetched_at_epoch": self.clock(), "status": None, "text": ""}
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                status, headers, body = self._request(current, 512 * 1024, robots=True)
                if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                    current = urllib.parse.urljoin(current, headers["Location"])
                    continue
                record["status"] = status
                record["text"] = body.decode("utf-8", errors="replace") if status == 200 else ""
                break
        except (FetchError, OSError) as error:
            record["error"] = str(error)
        return record

    # --- one request ---------------------------------------------------------------------------------
    def _turn(self, host: str, pace: float) -> None:
        """Wait until `pace` seconds have passed since this host's last request (any process)."""
        safe = host.replace(":", "_").replace("/", "_")
        stamp = self.cache.root / "robots" / f"{safe}.last"
        with self.cache.lock(f"host-{safe}"):
            last = float(stamp.read_text()) if stamp.is_file() else 0.0
            wait = last + pace - self.clock()
            if wait > 0:
                self.sleep(wait)
            stamp.write_text(repr(self.clock()))

    def _request(self, url: str, max_bytes: int, robots: bool = False) -> tuple[int, dict, bytes]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        pace = self.min_interval if robots else self._robots_for(f"{parsed.scheme}://{parsed.netloc}")[1]
        self._turn(host, pace)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        status, headers, body, error = None, {}, b"", None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                headers = dict(response.headers.items())
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as http_error:
            status = http_error.code
            headers = dict(http_error.headers.items()) if http_error.headers else {}
            body = b""
        except (urllib.error.URLError, OSError, ValueError) as network_error:
            # WHY ValueError: http.client refuses a malformed URL with InvalidURL, a ValueError.
            error = str(getattr(network_error, "reason", network_error))
        self.cache.append_jsonl("fetches.jsonl", {"at": iso_z(utc_now()), "url": url, "status": status,
                                                  "bytes": len(body), "error": error, "robots": robots})
        if error is not None:
            raise FetchError(f"{url}: {error}")
        if len(body) > max_bytes:
            raise FetchError(f"{url}: larger than {max_bytes} bytes")
        self.log(f"GET {url} -> {status} ({len(body)} bytes)")
        return status, headers, body


def ingest_local_file(cache: Cache, path: pathlib.Path) -> Fetched:
    """A file on disk: no URL to cite, so the retrieval records where it was read from and when."""
    data = pathlib.Path(path).read_bytes()
    media_type = media_type_of(None, data)
    if media_type not in CITABLE_MEDIA_TYPES:
        raise FetchError(f"{path}: {media_type} is not a source Markdownify reads ({CITABLE_MEDIA_TYPES})")
    resolved = str(pathlib.Path(path).resolve())
    sha256 = sha256_bytes(data)
    meta = cache.meta(sha256) if cache.has(sha256) else None
    for retrieval in (meta or {}).get("retrievals", []):
        if retrieval.get("local_file") == resolved:  # read before: one retrieval per file, not one per run
            return Fetched(url=None, final_url=None, sha256=sha256, media_type=meta["media_type"],
                           retrieved_at=retrieval["retrieved_at"], status=None, from_cache=True)
    retrieved_at = iso_z(utc_now())
    sha256 = cache.put(data, media_type, {"url": None, "final_url": None, "retrieved_at": retrieved_at,
                                          "http_status": None, "local_file": resolved})
    return Fetched(url=None, final_url=None, sha256=sha256, media_type=media_type,
                   retrieved_at=retrieved_at, status=None, from_cache=False)
