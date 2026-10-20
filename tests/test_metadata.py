"""Checks on the files that describe the integration rather than run it."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib

import pytest

from homeassistant.const import MAJOR_VERSION, MINOR_VERSION

REPO = Path(__file__).resolve().parents[1]


def test_the_version_is_declared_once():
    """manifest.json and pyproject.toml carry the same version.

    HACS shows the manifest's; a mismatch here would otherwise surface only
    when a release is tagged.
    """
    manifest = json.loads(
        (REPO / "custom_components" / "youtube_on_tv" / "manifest.json").read_text(
            "utf-8"
        )
    )
    project = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))

    assert manifest["version"] == project["project"]["version"]


def _minimum_home_assistant() -> tuple[int, int]:
    """Return the oldest supported release from hacs.json, as (year, month)."""
    hacs = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))
    year, month, *_ = hacs["homeassistant"].split(".")
    return int(year), int(month)


def test_runs_on_a_supported_home_assistant():
    """A release older than hacs.json's minimum means a stale pin somewhere."""
    running = (MAJOR_VERSION, MINOR_VERSION)
    assert running >= _minimum_home_assistant()


@pytest.mark.skipif(
    os.environ.get("HA_MIN") != "true", reason="only in the min job, which sets it"
)
def test_the_min_job_runs_the_minimum_from_hacs_json():
    """The min job's harness pin lives in the workflow, apart from hacs.json.

    Raising or lowering one without the other would test a release nobody
    promised; this catches it in the job itself.
    """
    running = (MAJOR_VERSION, MINOR_VERSION)
    assert running == _minimum_home_assistant()
