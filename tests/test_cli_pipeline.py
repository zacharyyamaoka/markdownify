"""`mdify --pipeline A|B|AB`: argument parsing and routing, with the vlm call and the pipeline-A build mocked (no model)."""

import types

import pytest

from mdify import bundle, cli, vlm

KPIS = {"coverage": 1.0, "traceability": 1.0, "silently_lost": 0, "unlinked": 0, "inexact_words": 0,
        "time": {"pages": 1, "seconds": 0.1, "cold": True, "model_load_seconds": 0.0}}


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen = []

    def fake(name):
        def build(target, out=None, **options):
            seen.append((name, target, options))
            return types.SimpleNamespace(directory=tmp_path, kpis=KPIS)
        return build

    monkeypatch.setattr(vlm, "build", fake("vlm"))
    monkeypatch.setattr(bundle, "build", fake("bundle"))
    return seen


def test_default_is_pipeline_a(calls):
    assert cli.main(["x.pdf"]) == 0
    assert [name for name, *_ in calls] == ["bundle"]


def test_model_with_pipeline_a_is_refused(calls, capsys):
    for argv in (["x.pdf", "--model", "codex"], ["x.pdf", "--pipeline", "A", "--model", "claude"]):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(argv)
        assert exit_info.value.code == 2
    assert "--model needs --pipeline B or AB" in capsys.readouterr().err
    assert calls == []


def test_pipeline_b_routes_to_vlm_with_chars(calls):
    assert cli.main(["x.pdf", "--pipeline", "B"]) == 0
    [(name, target, options)] = calls
    assert (name, target, options["backend"], options["place"]) == ("vlm", "x.pdf", "claude", "chars")


def test_pipeline_ab_routes_to_vlm_with_words_and_model(calls):
    assert cli.main(["x.png", "--pipeline", "AB", "--model", "codex"]) == 0
    [(name, _, options)] = calls
    assert (name, options["backend"], options["place"]) == ("vlm", "codex", "words")


def test_unknown_pipeline_is_refused(calls):
    with pytest.raises(SystemExit):
        cli.main(["x.pdf", "--pipeline", "C"])
    assert calls == []
