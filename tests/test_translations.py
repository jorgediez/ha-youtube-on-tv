"""Checks on the translations."""

from __future__ import annotations

import json
from pathlib import Path
import re

INTEGRATION = (
    Path(__file__).resolve().parents[1] / "custom_components" / "youtube_on_tv"
)


def test_every_raised_error_has_a_message():
    """A missing message shows the bare translation key to the user.

    It also fails on a message no code raises any more, so none is left behind.
    """
    raised = {
        match
        for path in INTEGRATION.glob("*.py")
        for match in re.findall(
            r'translation_domain=DOMAIN,\s*translation_key="(\w+)"',
            path.read_text(encoding="utf-8"),
        )
    }
    strings = json.loads(
        (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert raised, "the scan found no translated errors"
    assert raised == set(strings["exceptions"])
