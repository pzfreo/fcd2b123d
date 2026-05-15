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

# Glob both subdirectories — all current tier-2 fixtures are supported.
# Future fixtures that hit unsupported features can be temporarily skipped via
# an excluded-names tuple if needed.
TIER2_FIXTURES = sorted(
    p
    for d in Path("tests/fixtures").glob("tier2_*")
    for p in d.glob("*.FCStd")
)


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

    part = namespace["result"]
    actual = extract_build123d(part)
    expected = Properties.from_file(fcstd_path.with_suffix(".expected.json"))
    assert_equivalent(
        actual, expected,
        actual_part=part,
        pointcloud_path=fcstd_path.with_suffix(".pointcloud.json"),
    )
