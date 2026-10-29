"""`mdify view`: the server and the command, without a browser (tests/test_view_journey.py drives the page).
Run: uv run pytest tests/test_view.py"""

from __future__ import annotations

import hashlib
import json
import pathlib
import threading
import types
import urllib.error
import urllib.request

import pytest

from mdify import bundle, cli, view

MD = "# Title\n\nRated torque 8.3 Nm\n"


def make_bundle(directory: pathlib.Path) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source.html").write_text("<p>Rated torque 8.3 Nm</p>", encoding="utf-8")
    (directory / "source.json").write_text(json.dumps({"sha256": "ab" * 32, "media_type": "text/html", "retrievals": [
        {"url": "https://maker.example/p/ak70-10", "local_file": None}]}), encoding="utf-8")
    (directory / "document.md").write_text(MD, encoding="utf-8")
    (directory / "index.json").write_text(json.dumps({"source": {"sha256": "ab" * 32, "media_type": "text/html"},
                                                      "words": [], "blocks": [], "pages": []}), encoding="utf-8")
    (directory / "ledger.json").write_text("{}", encoding="utf-8")
    (directory / "kpis.json").write_text("{}", encoding="utf-8")
    return directory


@pytest.fixture
def served(tmp_path):
    server = view.Server(make_bundle(tmp_path / "bundle"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def get(server, path: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + path) as reply:
            return reply.status, reply.headers["Content-Type"], reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers["Content-Type"], error.read()


def test_serves_the_page_its_modules_and_the_bundle(served):
    status, content_type, body = get(served, "/")
    assert status == 200 and content_type.startswith("text/html") and b'src="/static/app.js"' in body
    for name in ("app.js", "markdown.js", "layout.js", "source.js", "vendor/source-highlighter.js", "vendor/lezer/markdown.js",
                 "vendor/pdfjs/pdf.min.mjs"):
        status, content_type, _ = get(served, f"/static/{name}")
        assert (status, content_type.split(";")[0]) == (200, "text/javascript"), name
    assert get(served, "/static/viewer.css")[1].startswith("text/css")
    status, _, body = get(served, "/bundle/document.md")
    assert (status, body.decode("utf-8")) == (200, MD)
    assert get(served, "/bundle/source.html")[0] == 200


def test_manifest_names_the_source(served):
    status, content_type, body = get(served, "/bundle")
    assert status == 200 and content_type.startswith("application/json")
    manifest = json.loads(body)
    assert manifest["sha256"] == "ab" * 32
    assert (manifest["media_type"], manifest["source"], manifest["name"]) == ("text/html", "source.html", "ak70-10")
    assert "document.md" in manifest["files"]


def test_refuses_paths_out_of_the_bundle_and_the_viewer(served, tmp_path):
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    for path in ("/bundle/../secret.txt", "/bundle/%2e%2e/secret.txt", "/static/../view.py", "/static/%2e%2e/view.py",
                 "/bundle/nothing.json", "/elsewhere"):
        assert get(served, path)[0] == 404, path


def test_an_incomplete_bundle_is_refused(tmp_path):
    directory = make_bundle(tmp_path / "bundle")
    (directory / "ledger.json").unlink()
    with pytest.raises(view.NotABundle, match="ledger.json"):
        view.Server(directory)


def test_view_of_a_bundle_does_not_convert(monkeypatch, tmp_path, capsys):
    directory = make_bundle(tmp_path / "bundle")
    monkeypatch.setattr(bundle, "build", lambda *a, **k: pytest.fail("a bundle directory must open as is"))
    monkeypatch.setattr(view.Server, "serve_forever", lambda self: None)
    assert cli.main(["view", str(directory)]) == 0
    assert "http://127.0.0.1:" in capsys.readouterr().out


@pytest.mark.parametrize("pipeline, routed", [("A", "bundle"), ("AB", "vlm")])
def test_view_of_a_source_converts_it_first_as_mdify_does(monkeypatch, tmp_path, pipeline, routed):
    directory = make_bundle(tmp_path / "bundle")
    seen = []

    def fake(name):
        def build(target, out=None, **options):
            seen.append((name, target, out, options))
            return types.SimpleNamespace(directory=directory, kpis={})
        return build

    from mdify import vlm

    monkeypatch.setattr(bundle, "build", fake("bundle"))
    monkeypatch.setattr(vlm, "build", fake("vlm"))
    monkeypatch.setattr(view.Server, "serve_forever", lambda self: None)
    assert cli.main(["view", "datasheet.pdf", "--pipeline", pipeline]) == 0
    [(name, target, out, _)] = seen
    assert (name, target, out) == (routed, "datasheet.pdf", None)


def test_view_of_a_missing_file_fails_cleanly(capsys):
    assert cli.main(["view", "/nonexistent/datasheet.pdf"]) == 1
    assert "mdify view: /nonexistent/datasheet.pdf" in capsys.readouterr().err


def test_vendored_modules_match_their_record():
    vendor = view.VIEWER / "vendor"
    record = json.loads((vendor / "VENDOR.json").read_text(encoding="utf-8"))["files"]
    for name, entry in record.items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == entry["sha256"], name

