"""Smoke tests verifying the package itself is importable."""

from __future__ import annotations

import loxone_voice


def test_version_is_set() -> None:
    assert isinstance(loxone_voice.__version__, str)
    assert loxone_voice.__version__.count(".") >= 2
