"""The ruler's tests render the cases themselves (about 4 s for all twelve) into a temporary directory, so they
never depend on, or overwrite, harness/out."""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import render  # noqa: E402


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> pathlib.Path:
    out = tmp_path_factory.mktemp("harness-out")
    results = render.render([], all_cases=True, out_root=out)
    failed = {case: result.get("error") for case, result in results.items() if not result.get("passed")}
    assert not failed, f"cases failed their self-check: {failed}"
    return out
