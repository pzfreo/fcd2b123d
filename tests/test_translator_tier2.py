"""End-to-end translator tests for tier-2 fixtures.

Same pattern as test_translator_tier1: translate in a FreeCAD subprocess,
exec the emitted Python in the test process, compare extracted properties
to the committed snapshot.

Tier-2 v1 only handles single-Pad bodies with line-segment sketches on the
XY plane. Fixtures requiring more (partdesign_example, with 3 Pockets) are
explicitly excluded by name until the corresponding tier-2.X PR lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from tests.utils.compare import assert_equivalent, extract_build123d
from tests.utils.properties import Properties

# Explicit list rather than glob: tier-2 lands incrementally, and a fixture
# may exist in the directory before its support does. Each PR that adds a
# tier-2 capability also adds the fixture name here.
TIER2_FIXTURES = [
    Path("tests/fixtures/tier2_partdesign/simple_pad.FCStd"),
]


@pytest.mark.parametrize("fcstd_path", TIER2_FIXTURES, ids=lambda p: p.stem)
def test_tier2_translation(fcstd_path: Path):
    source = _translate(fcstd_path)

    namespace: dict = {}
    try:
        exec(source, namespace)
    except Exception as exc:
        pytest.fail(f"Generated source failed to execute: {exc}\n\nSource:\n{source}")

    assert "result" in namespace, (
        "Translator must bind the final shape to `result`.\n\nSource:\n" + source
    )

    actual = extract_build123d(namespace["result"])
    expected = Properties.from_file(fcstd_path.with_suffix(".expected.json"))
    assert_equivalent(actual, expected)
