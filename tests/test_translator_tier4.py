"""End-to-end translator tests for tier-4 patterns (Linear / Polar / Mirrored).

Each fixture exercises one pattern type with a single Pad or Pocket Original.
The translator emits the pattern as the running body plus / minus the
Original's prism placed at each transformed location.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from tests.utils.compare import assert_equivalent, extract_build123d
from tests.utils.properties import Properties

FIXTURE_DIR = Path("tests/fixtures/tier4_patterns")

# drilled_plate has Ellipse sketch geometry (not yet supported); kept as a
# committed fixture so the snapshot stays reproducible, but excluded from the
# tier-4 pattern assertion until Ellipse lands.
EXCLUDED = {"drilled_plate"}

FIXTURES = sorted(
    p for p in FIXTURE_DIR.glob("*.FCStd") if p.stem not in EXCLUDED
)


@pytest.mark.parametrize("fcstd_path", FIXTURES, ids=lambda p: p.stem)
def test_tier4_translation(fcstd_path: Path):
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
